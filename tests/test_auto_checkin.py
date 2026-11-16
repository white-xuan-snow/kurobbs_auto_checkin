import tests._stubs  # noqa: F401  (must precede project module imports)

import contextlib
import io
import json
import unittest
from unittest.mock import MagicMock, patch

import requests

import auto_checkin
import logging_utils
from auto_checkin import (
    KurobbsClient,
    KurobbsClientException,
    Response,
    format_report,
    run,
)
from settings import Settings, collect_sensitive_values

TOKEN_A = "tk_alpha_0001"
TOKEN_B = "tk_beta_0002"

MINE_URL = KurobbsClient.USER_MINE_URL
ROLE_URL = KurobbsClient.FIND_ROLE_LIST_API_URL
SIGN_URL = KurobbsClient.SIGN_URL
USER_SIGN_URL = KurobbsClient.USER_SIGN_URL

ROUTES = {
    MINE_URL: {
        "code": 0,
        "msg": "OK",
        "success": True,
        "data": {"mine": {"userId": 123, "userName": "小明"}},
    },
    ROLE_URL: {
        "code": 0,
        "msg": "OK",
        "success": True,
        "data": {
            "defaultRoleList": [
                {"gameId": 2, "serverId": 1, "roleId": 9, "userId": 123}
            ]
        },
    },
    SIGN_URL: {"code": 0, "msg": "OK", "success": True, "data": {}},
    USER_SIGN_URL: {"code": 0, "msg": "OK", "success": True, "data": {}},
}


def _response(payload):
    """Build a response object shaped like requests.Response for _post()."""
    resp = MagicMock()
    resp.content = json.dumps(payload).encode()
    resp.raise_for_status.return_value = None
    return resp


def _router(calls=None, routes=None):
    """Return a requests.Session.post side effect that dispatches on the URL."""
    table = ROUTES if routes is None else routes

    def fake_post(url, data=None, timeout=None, **kwargs):
        if calls is not None:
            calls.append(url)
        return _response(table[url])

    return fake_post


def _recording_checkin(store):
    """Wrap the real checkin_account so run() results can be inspected."""
    real = auto_checkin.checkin_account

    def recording(index, token):
        result = real(index, token)
        store.append(result)
        return result

    return recording


class AccountCheckinTest(unittest.TestCase):
    def setUp(self):
        self.post_urls = []
        patcher = patch("requests.Session.post")
        self.mock_post = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_post.side_effect = _router(self.post_urls)

    def test_two_successful_accounts_return_zero_with_eight_posts(self):
        results = []
        notifier = MagicMock()
        with patch("auto_checkin.checkin_account", side_effect=_recording_checkin(results)):
            exit_code = run([TOKEN_A, TOKEN_B], notifier)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(results), 2)
        self.assertEqual([result.ok for result in results], [True, True])
        self.assertEqual([result.index for result in results], [1, 2])
        self.assertEqual(self.mock_post.call_count, 8)
        self.assertEqual(len(self.post_urls), 8)
        notifier.send.assert_called_once()

    def test_first_account_failure_is_isolated_from_the_second(self):
        mine_attempts = {"count": 0}
        urls = []

        def flaky_post(url, data=None, timeout=None, **kwargs):
            urls.append(url)
            if url == MINE_URL:
                mine_attempts["count"] += 1
                if mine_attempts["count"] == 1:
                    raise requests.ConnectionError("connection reset by peer")
            return _response(ROUTES[url])

        self.mock_post.side_effect = flaky_post

        results = []
        notifier = MagicMock()
        with patch("auto_checkin.checkin_account", side_effect=_recording_checkin(results)):
            exit_code = run([TOKEN_A, TOKEN_B], notifier)

        self.assertEqual(exit_code, 1)
        self.assertEqual([result.ok for result in results], [False, True])
        self.assertEqual(results[0].label, "账号#1")
        self.assertIn("failed", results[0].message)
        # The second account really reached the API and signed in both ways.
        self.assertEqual(mine_attempts["count"], 2)
        self.assertEqual(urls.count(USER_SIGN_URL), 1)
        self.assertEqual(urls.count(SIGN_URL), 1)
        notifier.send.assert_called_once()

    def test_all_accounts_failing_still_returns_one(self):
        self.mock_post.side_effect = requests.ConnectionError("offline")

        results = []
        notifier = MagicMock()
        with patch("auto_checkin.checkin_account", side_effect=_recording_checkin(results)):
            exit_code = run([TOKEN_A, TOKEN_B], notifier)

        self.assertEqual(exit_code, 1)
        self.assertEqual([result.ok for result in results], [False, False])
        self.assertEqual(self.mock_post.call_count, 2)
        notifier.send.assert_called_once()

    def test_format_report_has_each_label_and_no_raw_token(self):
        mine_attempts = {"count": 0}

        def second_account_fails(url, data=None, timeout=None, **kwargs):
            if url == MINE_URL:
                mine_attempts["count"] += 1
                if mine_attempts["count"] == 2:
                    raise requests.ConnectionError("boom")
            return _response(ROUTES[url])

        self.mock_post.side_effect = second_account_fails

        results = []
        with patch("auto_checkin.checkin_account", side_effect=_recording_checkin(results)):
            run([TOKEN_A, TOKEN_B], MagicMock())

        report = format_report(results)
        self.assertIn("小明", report)
        self.assertIn("账号#2", report)
        self.assertIn("共 2 个账号：成功 1，失败 1", report)
        self.assertNotIn(TOKEN_A, report)
        self.assertNotIn(TOKEN_B, report)
        self.assertEqual(format_report([]), "")


class AlreadySignedTest(unittest.TestCase):
    """code=1511 ("请勿重复签到") is an idempotent success, not a failure."""

    ALREADY_SIGNED = {"code": 1511, "success": False, "msg": "请勿重复签到"}

    def setUp(self):
        patcher = patch("requests.Session.post")
        self.mock_post = patcher.start()
        self.addCleanup(patcher.stop)

    def _routes(self, overrides=None):
        routes = dict(ROUTES)
        routes.update(overrides or {})
        self.mock_post.side_effect = _router(routes=routes)
        return routes

    def test_both_sign_actions_already_signed_exit_zero(self):
        self._routes(
            {SIGN_URL: dict(self.ALREADY_SIGNED), USER_SIGN_URL: dict(self.ALREADY_SIGNED)}
        )

        results = []
        notifier = MagicMock()
        with patch("auto_checkin.checkin_account", side_effect=_recording_checkin(results)):
            exit_code = run([TOKEN_A], notifier)

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(results), 1)
        self.assertTrue(results[0].ok)
        self.assertIn("今日已签到", results[0].message)
        notifier.send.assert_called_once()
        report = notifier.send.call_args[0][0]
        self.assertIn("今日已签到", report)
        self.assertIn("成功 1，失败 0", report)

    def test_already_signed_does_not_mask_an_expired_token(self):
        mine_calls = {"count": 0}

        def routed_post(url, data=None, timeout=None, **kwargs):
            if url == MINE_URL:
                mine_calls["count"] += 1
                if mine_calls["count"] == 2:
                    return _response(
                        {"code": 220, "success": False, "msg": "登录已过期，请重新登录"}
                    )
            if url in (SIGN_URL, USER_SIGN_URL):
                return _response(dict(self.ALREADY_SIGNED))
            return _response(ROUTES[url])

        self.mock_post.side_effect = routed_post

        results = []
        notifier = MagicMock()
        with patch("auto_checkin.checkin_account", side_effect=_recording_checkin(results)):
            exit_code = run([TOKEN_A, TOKEN_B], notifier)

        self.assertEqual(exit_code, 1)
        self.assertEqual([result.ok for result in results], [True, False])
        self.assertIn("今日已签到", results[0].message)
        self.assertIn("code=220", results[1].message)
        self.assertIn("token 已失效", results[1].message)
        self.assertEqual(mine_calls["count"], 2)

    def test_expired_token_is_not_treated_as_already_signed(self):
        client = KurobbsClient(TOKEN_A)
        resp = Response(code=220, msg="登录已过期，请重新登录", success=False)
        self.assertFalse(client._is_already_signed(resp))
        self.assertTrue(
            client._is_already_signed(Response(code=1511, msg="", success=False))
        )


class ExpiredTokenTest(unittest.TestCase):
    def setUp(self):
        patcher = patch("requests.Session.post")
        self.mock_post = patcher.start()
        self.addCleanup(patcher.stop)

    def test_mine_code_220_raises_with_code_in_message(self):
        routes = dict(ROUTES)
        routes[MINE_URL] = {"code": 220, "success": False, "msg": "登录已过期，请重新登录"}
        self.mock_post.side_effect = _router(routes=routes)

        client = KurobbsClient(TOKEN_A)
        with self.assertRaises(KurobbsClientException) as caught:
            client.get_mine_info()

        message = str(caught.exception)
        self.assertIn("code=220", message)
        self.assertIn("token 已失效", message)


class NicknameTest(unittest.TestCase):
    def _nickname_for(self, mine_payload):
        routes = dict(ROUTES)
        routes[MINE_URL] = mine_payload
        with patch("requests.Session.post", side_effect=_router(routes=routes)):
            client = KurobbsClient(TOKEN_A)
            self.assertIsNone(client.nickname)
            client.get_mine_info()
            return client.nickname

    def test_nickname_comes_from_cached_mine_info(self):
        with patch("requests.Session.post", side_effect=_router()):
            client = KurobbsClient(TOKEN_A)
            self.assertIsNone(client.nickname)
            client.get_mine_info()
            self.assertEqual(client.nickname, "小明")

    def test_nickname_uses_user_name_from_live_payload(self):
        nickname = self._nickname_for(
            {
                "code": 200,
                "msg": "请求成功",
                "success": True,
                "data": {"mine": {"userId": 30188029, "userName": "用户A", "level": 5}},
            }
        )
        self.assertEqual(nickname, "用户A")

    def test_nickname_falls_back_to_legacy_field(self):
        nickname = self._nickname_for(
            {
                "code": 200,
                "msg": "请求成功",
                "success": True,
                "data": {"mine": {"userId": 123, "nickname": "旧字段"}},
            }
        )
        self.assertEqual(nickname, "旧字段")


class LogRedactionTest(unittest.TestCase):
    def test_token_never_reaches_stdout_after_configure_logger(self):
        env = {"TOKENS": f"{TOKEN_A},{TOKEN_B}"}
        logging_utils.configure_logger(
            debug=False, secrets=collect_sensitive_values(env)
        )

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            logging_utils.logger.info(TOKEN_A)
            logging_utils.logger.error(TOKEN_B)
        output = buffer.getvalue()

        self.assertTrue(output.strip())
        self.assertNotIn(TOKEN_A, output)
        self.assertNotIn(TOKEN_B, output)


class MainEntryTest(unittest.TestCase):
    def setUp(self):
        self.post_urls = []
        patcher = patch("requests.Session.post")
        self.mock_post = patcher.start()
        self.addCleanup(patcher.stop)
        self.mock_post.side_effect = _router(self.post_urls)

    def _call_main(self):
        """Run main() with settings and the notifier faked; return (exit code, notifier)."""
        notifier = MagicMock()
        with patch(
            "auto_checkin.Settings.load",
            return_value=Settings(tokens=[TOKEN_A, TOKEN_B]),
        ), patch("auto_checkin.NotificationService", return_value=notifier):
            with self.assertRaises(SystemExit) as caught:
                auto_checkin.main()
        return caught.exception.code, notifier

    def test_main_exits_zero_when_all_accounts_succeed(self):
        exit_code, notifier = self._call_main()

        self.assertEqual(exit_code, 0)
        self.assertEqual(notifier.send.call_count, 1)
        report = notifier.send.call_args[0][0]
        self.assertIn("小明", report)
        self.assertNotIn(TOKEN_A, report)
        self.assertNotIn(TOKEN_B, report)

    def test_main_exits_one_when_any_account_fails(self):
        mine_attempts = {"count": 0}

        def first_account_fails(url, data=None, timeout=None, **kwargs):
            if url == MINE_URL:
                mine_attempts["count"] += 1
                if mine_attempts["count"] == 1:
                    raise requests.ConnectionError("boom")
            return _response(ROUTES[url])

        self.mock_post.side_effect = first_account_fails
        exit_code, notifier = self._call_main()

        self.assertEqual(exit_code, 1)
        self.assertEqual(notifier.send.call_count, 1)
        self.assertIn("账号#1", notifier.send.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
