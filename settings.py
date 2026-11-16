import os
import re
from dataclasses import dataclass
from typing import Iterable, List, Mapping, Optional

TOKEN_LIST_ENV = "TOKENS"
TOKEN_ENV = "TOKEN"
BARK_DEVICE_KEY_ENV = "BARK_DEVICE_KEY"
BARK_SERVER_URL_ENV = "BARK_SERVER_URL"
SERVER3_SEND_KEY_ENV = "SERVER3_SEND_KEY"
_INDEXED_TOKEN_RE = re.compile(r"^TOKEN_(\d+)$")
_TOKEN_SEPARATOR_RE = re.compile(r"[,;\s]+")


class SettingsError(Exception):
    """Raised when required settings are missing or invalid."""


def parse_bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _dedupe(values: Iterable[Optional[str]]) -> List[str]:
    """Drop empty values and duplicates, keeping the first occurrence order."""
    seen = set()
    result: List[str] = []
    for value in values:
        if not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def split_tokens(raw: Optional[str]) -> List[str]:
    """Split a delimited token string into a deduplicated, order-preserving list."""
    if not raw:
        return []
    return _dedupe(part.strip() for part in _TOKEN_SEPARATOR_RE.split(raw))


def load_tokens(environ: Mapping[str, str] = os.environ) -> List[str]:
    """Collect tokens from TOKENS, then TOKEN_<n> by numeric order, then TOKEN."""
    tokens: List[str] = []
    tokens.extend(split_tokens(environ.get(TOKEN_LIST_ENV)))

    indexed = []
    for key, value in environ.items():
        match = _INDEXED_TOKEN_RE.match(key)
        if match:
            indexed.append((int(match.group(1)), value))
    for _, value in sorted(indexed, key=lambda item: item[0]):
        tokens.extend(split_tokens(value))

    tokens.extend(split_tokens(environ.get(TOKEN_ENV)))
    return _dedupe(tokens)


def collect_sensitive_values(environ: Mapping[str, str] = os.environ) -> List[str]:
    """Values to redact from logs, available before Settings.load()."""
    values: List[str] = []
    values.extend(load_tokens(environ))
    values.append(environ.get(TOKEN_LIST_ENV))
    values.append(environ.get(TOKEN_ENV))
    values.append(environ.get(BARK_DEVICE_KEY_ENV))
    values.append(environ.get(BARK_SERVER_URL_ENV))
    values.append(environ.get(SERVER3_SEND_KEY_ENV))
    return _dedupe(values)


@dataclass
class Settings:
    tokens: List[str]
    debug: bool = False
    bark_device_key: Optional[str] = None
    bark_server_url: Optional[str] = None
    server3_send_key: Optional[str] = None

    @classmethod
    def load(cls) -> "Settings":
        tokens = load_tokens(os.environ)
        if not tokens:
            raise SettingsError("TOKENS or TOKEN is required but missing.")

        return cls(
            tokens=tokens,
            debug=parse_bool(os.getenv("DEBUG", "")),
            bark_device_key=os.getenv(BARK_DEVICE_KEY_ENV),
            bark_server_url=os.getenv(BARK_SERVER_URL_ENV),
            server3_send_key=os.getenv(SERVER3_SEND_KEY_ENV),
        )

    def sensitive_values(self) -> List[str]:
        """Values that should be redacted from logs."""
        values: List[str] = list(self.tokens)
        for token in self.tokens:
            values.extend(split_tokens(token))
        values.append(self.bark_device_key)
        values.append(self.bark_server_url)
        values.append(self.server3_send_key)
        return _dedupe(values)
