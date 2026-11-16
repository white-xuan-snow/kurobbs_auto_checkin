import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence
from zoneinfo import ZoneInfo

import requests
from loguru import logger
from pydantic import BaseModel, Field

from ext_notification import NotificationService
from logging_utils import configure_logger
from settings import (
    Settings,
    SettingsError,
    collect_sensitive_values,
    parse_bool,
)


class Response(BaseModel):
    code: int = Field(..., alias="code", description="返回值")
    msg: str = Field(..., alias="msg", description="提示信息")
    success: Optional[bool] = Field(None, alias="success", description="token有时才有")
    data: Optional[Any] = Field(None, alias="data", description="请求成功才有")


class KurobbsClientException(Exception):
    """Custom exception for Kurobbs client errors."""


# "Already signed in today" is an idempotent success, not a failure: the API
# reports it as code=1511 / success=false with a "请勿重复签到" style message.
ALREADY_SIGNED_CODES = frozenset({1511})
_ALREADY_SIGNED_MSG = re.compile(r"已签到|已经签到|重复签到|签到过了")


def _beijing_timezone():
    """Asia/Shanghai time zone, falling back to a fixed +08:00 offset.

    Some platforms (e.g. Windows without the tzdata package) ship no IANA time
    zone database, so ZoneInfo raises instead of resolving the key. The offset of
    Asia/Shanghai has been +08:00 without DST since 1991, so the fallback keeps
    the month used for ``reqMonth`` identical.
    """
    try:
        return ZoneInfo("Asia/Shanghai")
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError / ImportError on missing tzdata
        return timezone(timedelta(hours=8))


class KurobbsClient:
    FIND_ROLE_LIST_API_URL = "https://api.kurobbs.com/gamer/role/default"
    SIGN_URL = "https://api.kurobbs.com/encourage/signIn/v2"
    USER_SIGN_URL = "https://api.kurobbs.com/user/signIn"
    USER_MINE_URL = "https://api.kurobbs.com/user/mineV2"

    def __init__(self, token: str):
        if not token:
            raise KurobbsClientException("TOKEN is required to call Kurobbs APIs.")

        self.token = token
        self.session = requests.Session()
        self.session.headers.update(
            {
                "osversion": "Android",
                "devcode": "2fba3859fe9bfe9099f2696b8648c2c6",
                "countrycode": "CN",
                "ip": "10.0.2.233",
                "model": "2211133C",
                "source": "android",
                "lang": "zh-Hans",
                "version": "1.0.9",
                "versioncode": "1090",
                "token": self.token,
                "content-type": "application/x-www-form-urlencoded; charset=utf-8",
                "accept-encoding": "gzip",
                "user-agent": "okhttp/3.10.0",
            }
        )
        self.result: Dict[str, str] = {}
        self.exceptions: List[Exception] = []
        self._mine_info: Optional[Dict[str, Any]] = None

    def _post(self, url: str, data: Dict[str, Any]) -> Response:
        """Make a POST request to the specified URL with the given data."""
        try:
            response = self.session.post(url, data=data, timeout=15)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise KurobbsClientException(f"Request to {url} failed: {exc}") from exc

        try:
            res = Response.model_validate_json(response.content)
        except Exception as exc:  # noqa: BLE001
            raise KurobbsClientException(f"Failed to parse response from {url}") from exc

        logger.debug(
            "POST {} -> code={}, success={}, msg={}",
            url,
            res.code,
            res.success,
            res.msg,
        )
        return res

    def get_mine_info(self, type: int = 1) -> Dict[str, Any]:
        """Get mine info."""
        res = self._post(self.USER_MINE_URL, {"type": type})
        if not res.data:
            if res.code == 220:
                raise KurobbsClientException(
                    f"token 已失效（code=220），请重新获取: {res.msg}"
                )
            raise KurobbsClientException(
                f"User info is missing in response (code={res.code}, msg={res.msg})"
            )
        self._mine_info = res.data
        return res.data

    @property
    def nickname(self) -> Optional[str]:
        """Nickname from the cached mine info, or None when it is unavailable."""
        mine = (self._mine_info or {}).get("mine") or {}
        # The live API exposes `userName`; `nickname` stays as a fallback for
        # older payloads (and for the fixtures built before the rename).
        return mine.get("userName") or mine.get("nickname")

    def get_user_game_list(self, user_id: int) -> Dict[str, Any]:
        """Get the list of games for the user."""
        res = self._post(self.FIND_ROLE_LIST_API_URL, {"queryUserId": user_id})
        if not res.data:
            raise KurobbsClientException("User game list is missing in response.")
        return res.data

    def checkin(self) -> Response:
        """Perform the check-in operation."""
        mine_info = self.get_mine_info()
        user_game_list = self.get_user_game_list(user_id=mine_info.get("mine", {}).get("userId", 0))

        beijing_tz = _beijing_timezone()
        beijing_time = datetime.now(beijing_tz)

        role_list = user_game_list.get("defaultRoleList") or []
        if not role_list:
            raise KurobbsClientException("No default role found for the user.")
        role_info = role_list[0]

        data = {
            "gameId": role_info.get("gameId", 2),
            "serverId": role_info.get("serverId"),
            "roleId": role_info.get("roleId", 0),
            "userId": role_info.get("userId", 0),
            "reqMonth": f"{beijing_time.month:02d}",
        }
        return self._post(self.SIGN_URL, data)

    def sign_in(self) -> Response:
        """Perform the sign-in operation."""
        return self._post(self.USER_SIGN_URL, {"gameId": 2})

    def _is_already_signed(self, resp: Response) -> bool:
        """True when the response means "already signed in today".

        An expired token (code=220) must never be mistaken for an idempotent
        success, so the code set is explicit and the message regex only matches
        the "already signed" wording.
        """
        if resp.code in ALREADY_SIGNED_CODES:
            return True
        return bool(_ALREADY_SIGNED_MSG.search(resp.msg or ""))

    def _process_sign_action(
        self,
        action_name: str,
        action_method: Callable[[], Response],
        success_message: str,
        failure_message: str,
    ):
        """Handle the common logic for sign-in actions."""
        resp = action_method()
        if resp.success:
            self.result[action_name] = success_message
            logger.info("{} -> {}", action_name, success_message)
        elif self._is_already_signed(resp):
            self.result[action_name] = f"{action_name} 今日已签到"
            logger.info("{} -> 今日已签到，跳过", action_name)
        else:
            self.exceptions.append(KurobbsClientException(f"{failure_message}, {resp.msg}"))

    def start(self):
        """Start the sign-in process."""
        self._process_sign_action(
            action_name="checkin",
            action_method=self.checkin,
            success_message="签到奖励签到成功",
            failure_message="签到奖励签到失败",
        )

        self._process_sign_action(
            action_name="sign_in",
            action_method=self.sign_in,
            success_message="社区签到成功",
            failure_message="社区签到失败",
        )

        self._log()

    @property
    def msg(self) -> str:
        return ", ".join(self.result.values()) + "!" if self.result else ""

    def _log(self):
        """Log the results and raise exceptions if any."""
        if msg := self.msg:
            logger.info(msg)
        if self.exceptions:
            raise KurobbsClientException("; ".join(map(str, self.exceptions)))


@dataclass
class AccountResult:
    """Outcome of one account check-in."""

    index: int
    label: str
    ok: bool
    message: str


def checkin_account(index: int, token: str) -> AccountResult:
    """Check in a single account. Never raises."""
    label = f"账号#{index}"
    client: Optional[KurobbsClient] = None
    try:
        client = KurobbsClient(token)
        client.start()
    except KurobbsClientException as exc:
        return AccountResult(
            index=index,
            label=_account_label(client, index),
            ok=False,
            message=str(exc),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("账号 [{}] 签到时发生未预期异常: {}", label, exc)
        return AccountResult(
            index=index,
            label=_account_label(client, index),
            ok=False,
            message=str(exc),
        )

    return AccountResult(
        index=index,
        label=_account_label(client, index),
        ok=True,
        message=client.msg or "签到成功",
    )


def _account_label(client: Optional[KurobbsClient], index: int) -> str:
    """Nickname when the mine info was fetched, otherwise the positional label."""
    nickname = client.nickname if client is not None else None
    return nickname or f"账号#{index}"


def format_report(results: Sequence[AccountResult]) -> str:
    """Render the aggregated report. Never contains a raw token."""
    if not results:
        return ""

    lines = []
    for result in results:
        status = "成功" if result.ok else "失败"
        lines.append(f"[{result.index}] {result.label} -> {status}: {result.message}")

    ok_count = sum(1 for result in results if result.ok)
    fail_count = len(results) - ok_count
    lines.append(f"共 {len(results)} 个账号：成功 {ok_count}，失败 {fail_count}")
    return "\n".join(lines)


def run(tokens: Sequence[str], notifier: NotificationService) -> int:
    """Check in every account serially, push one report, return the exit code."""
    total = len(tokens)
    results: List[AccountResult] = []

    for index, token in enumerate(tokens, start=1):
        logger.info("===== 账号 [{}/{}] =====", index, total)
        result = checkin_account(index, token)
        results.append(result)
        if result.ok:
            logger.info("账号 [{}] 签到成功: {}", result.label, result.message)
        else:
            logger.error("账号 [{}] 签到失败: {}", result.label, result.message)

    if results:
        notifier.send(format_report(results))

    ok_count = sum(1 for result in results if result.ok)
    fail_count = len(results) - ok_count
    logger.info("全部完成：成功 {}，失败 {}", ok_count, fail_count)
    return 0 if fail_count == 0 else 1


def main():
    # Configure logging as early as possible so that every token is already redacted
    # before settings (and any log line mentioning them) come into play.
    configure_logger(
        debug=parse_bool(os.getenv("DEBUG", "")),
        secrets=collect_sensitive_values(),
    )

    try:
        settings = Settings.load()
    except SettingsError as exc:
        logger.error(str(exc))
        sys.exit(1)

    sys.exit(run(settings.tokens, NotificationService(settings)))


if __name__ == "__main__":
    main()
