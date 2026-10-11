"""Use synthetic keys only; real DPAPI tests write to temporary directories."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ironwill.credentials import CredentialError, KeyStore, _crypt, endpoint_identity, environment_key, read_legacy_key


class CredentialInputTests(unittest.TestCase):
    def test_legacy_environment_is_only_used_for_official_provider(self):
        with patch.dict(os.environ, {"IRONWILL_API_KEY": "", "DEEPSEEK_API_KEY": "test-legacy-key"}):
            self.assertEqual(environment_key("https://api.deepseek.com/v1")[0], "test-legacy-key")
            self.assertEqual(environment_key("https://different.invalid/v1"), ("", ""))
            self.assertEqual(environment_key("https://api.deepseek.com:8443/v1"), ("", ""))

    def test_explicit_environment_takes_precedence(self):
        with patch.dict(os.environ, {"IRONWILL_API_KEY": "test-current-key", "DEEPSEEK_API_KEY": "test-legacy-key"}):
            self.assertEqual(environment_key("https://api.deepseek.com/v1"), ("test-current-key", "IRONWILL_API_KEY"))

    def test_endpoint_alias_and_credentials_rejection(self):
        self.assertEqual(endpoint_identity("https://MODEL.invalid/v1/"), endpoint_identity("https://model.invalid/v1/chat/completions"))
        for endpoint in ("http://model.invalid/v1", "https://user:password@model.invalid/v1", "https://model.invalid/v1?key=x"):
            with self.assertRaises(CredentialError):
                endpoint_identity(endpoint)

    def test_import_is_bounded_single_line_and_does_not_modify_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "deepseek_key.txt"
            source.write_text("\ufefftest-legacy-key\n", encoding="utf-8")
            before = source.read_bytes()
            self.assertEqual(read_legacy_key(source), "test-legacy-key")
            self.assertEqual(source.read_bytes(), before)
            for invalid in ("key-one\nkey-two", "x" * 8193, ""):
                source.write_text(invalid, encoding="utf-8")
                with self.assertRaises(CredentialError):
                    read_legacy_key(source)

    def test_unsupported_platform_has_no_plaintext_fallback(self):
        with patch("ironwill.credentials.os.name", "posix"):
            with self.assertRaises(CredentialError):
                _crypt(b"synthetic-input")


@unittest.skipUnless(os.name == "nt", "Real DPAPI verification requires Windows")
class WindowsKeyStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ironwill-secret-test-")
        self.directory = Path(self.temporary.name)
        self.store = KeyStore(self.directory)
        self.endpoint = "https://model.invalid/v1"
        self.key = "SYNTHETIC-NOT-A-REAL-API-KEY-12345"

    def tearDown(self):
        self.temporary.cleanup()

    def test_new_instance_restores_without_plaintext_file(self):
        self.store.save(self.endpoint, self.key)
        self.assertNotIn(self.key.encode(), self.store.path.read_bytes())
        self.assertEqual(KeyStore(self.directory).load(self.endpoint), self.key)
        self.assertEqual(list(self.store.path.parent.iterdir()), [self.store.path])

    def test_key_is_bound_to_exact_service_endpoint(self):
        self.store.save(self.endpoint, self.key)
        self.assertIsNone(self.store.load("https://another.invalid/v1"))
        self.assertIsNone(self.store.load("https://model.invalid/another"))
        self.assertEqual(self.store.load(self.endpoint), self.key)

    def test_corrupt_ciphertext_fails_without_secret_in_error(self):
        self.store.path.parent.mkdir()
        self.store.path.write_bytes(b"corrupt-synthetic-data")
        with self.assertRaises(CredentialError) as failure:
            self.store.load(self.endpoint)
        self.assertNotIn("corrupt-synthetic-data", str(failure.exception))

    def test_encryption_failure_creates_no_file(self):
        with patch("ironwill.credentials._crypt", side_effect=CredentialError("test failure")):
            with self.assertRaises(CredentialError):
                self.store.save(self.endpoint, self.key)
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_failed_atomic_replace_preserves_previous_key(self):
        self.store.save(self.endpoint, self.key)
        original = self.store.path.read_bytes()
        with patch("ironwill.credentials.os.replace", side_effect=OSError("test failure")):
            with self.assertRaises(OSError):
                self.store.save(self.endpoint, "SYNTHETIC-REPLACEMENT-KEY")
        self.assertEqual(self.store.path.read_bytes(), original)
        self.assertEqual(self.store.load(self.endpoint), self.key)
        self.assertEqual(list(self.store.path.parent.iterdir()), [self.store.path])

    def test_delete_is_idempotent_and_missing_key_is_none(self):
        self.assertIsNone(self.store.load(self.endpoint))
        self.store.save(self.endpoint, self.key)
        self.store.delete()
        self.store.delete()
        self.assertFalse(self.store.exists())
        self.assertIsNone(self.store.load(self.endpoint))
