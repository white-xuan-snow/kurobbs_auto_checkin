import tests._stubs  # noqa: F401  (must precede project module imports)

import os
import unittest
from unittest.mock import patch

import settings
from settings import (
    Settings,
    SettingsError,
    collect_sensitive_values,
    load_tokens,
    split_tokens,
)


class SplitTokensTest(unittest.TestCase):
    def test_split_tokens_splits_on_comma_semicolon_whitespace_and_dedupes(self):
        self.assertEqual(split_tokens("a,b\nc; a"), ["a", "b", "c"])

    def test_split_tokens_returns_empty_list_for_none_and_blank_input(self):
        self.assertEqual(split_tokens(None), [])
        self.assertEqual(split_tokens("  , ;\n "), [])


class LoadTokensTest(unittest.TestCase):
    def test_load_tokens_merges_tokens_indexed_and_legacy_in_order(self):
        environ = {"TOKENS": "t1,t2", "TOKEN_2": "t3", "TOKEN": "t1"}
        self.assertEqual(load_tokens(environ), ["t1", "t2", "t3"])

    def test_load_tokens_sorts_indexed_tokens_numerically(self):
        environ = {"TOKEN_10": "t10", "TOKEN_2": "t2"}
        self.assertEqual(load_tokens(environ), ["t2", "t10"])


class SettingsLoadTest(unittest.TestCase):
    def test_settings_load_raises_settings_error_when_no_token_present(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SettingsError):
                Settings.load()

    def test_settings_load_reads_tokens_list_and_debug_bool_from_environ(self):
        env = {"TOKENS": "tk_a,tk_b", "TOKEN": "tk_a", "DEBUG": "true"}
        with patch.dict(os.environ, env, clear=True):
            loaded = Settings.load()
        self.assertEqual(loaded.tokens, ["tk_a", "tk_b"])
        self.assertIs(loaded.debug, True)

    def test_collect_sensitive_values_covers_each_split_token_and_push_configs(self):
        env = {
            "TOKENS": "tk_a,tk_b",
            "BARK_DEVICE_KEY": "bark-device",
            "BARK_SERVER_URL": "https://bark.example.com",
            "SERVER3_SEND_KEY": "sct-key",
        }
        values = collect_sensitive_values(env)
        self.assertIn("tk_a", values)
        self.assertIn("tk_b", values)
        self.assertIn("bark-device", values)
        self.assertIn("https://bark.example.com", values)
        self.assertIn("sct-key", values)


if __name__ == "__main__":
    unittest.main()
