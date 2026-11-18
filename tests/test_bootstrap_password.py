"""Exercise interrupted password handoffs without changing local accounts."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SOURCE = Path(__file__).resolve().parents[1] / "roles/dev_host/files/bootstrap_password.py"
SPEC = importlib.util.spec_from_file_location("bootstrap_password", SOURCE)
handoff = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(handoff)


class PasswordHandoffTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.pending = Path(temporary.name) / "pending"
        self.users = {"root": 0, "initial": 1001, "dev": 1002, "other": 1003}
        self.addCleanup(patch.stopall)
        patch.object(handoff, "PENDING", self.pending).start()
        patch.object(handoff.pwd, "getpwnam", side_effect=lambda name: SimpleNamespace(pw_uid=self.users[name])).start()
        self.passwd = patch.object(handoff.subprocess, "run").start()

    def test_passwordless_setup_has_nothing_to_retire(self):
        self.assertFalse(handoff.finish("dev"))
        self.assertFalse(self.pending.exists())
        self.passwd.assert_not_called()

    def test_record_is_idempotent_and_only_applies_to_the_intended_development_account(self):
        self.assertTrue(handoff.record("initial", "dev"))
        self.assertFalse(handoff.record("initial", "dev"))
        self.assertFalse(handoff.finish("other"))
        self.passwd.assert_not_called()
        self.assertTrue(handoff.finish("dev"))
        self.assertFalse(handoff.finish("dev"))
        self.assertEqual(self.passwd.call_args.args[0], ["/usr/bin/passwd", "--lock", "--", "initial"])
        self.passwd.assert_called_once()

    def test_id_change_or_shared_uid_cannot_lock_another_account(self):
        handoff.record("initial", "dev")
        for uid in (999, self.users["dev"]):
            self.users["initial"] = uid
            with self.assertRaisesRegex(ValueError, "identity changed"):
                handoff.finish("dev")
        self.passwd.assert_not_called()

    def test_shared_account_cannot_be_recorded(self):
        with self.assertRaisesRegex(ValueError, "distinct"):
            handoff.record("dev", "dev")
        self.assertFalse(self.pending.exists())

    def test_untrusted_state_directory_refuses_to_record_or_lock(self):
        self.pending.mkdir(mode=0o755)
        with self.assertRaisesRegex(ValueError, "private directory"):
            handoff.record("root", "dev")
        with self.assertRaisesRegex(ValueError, "private directory"):
            handoff.finish("dev")
        self.passwd.assert_not_called()

    def test_failed_lock_is_retried(self):
        handoff.record("root", "dev")
        self.passwd.side_effect = subprocess.CalledProcessError(1, "passwd")
        with self.assertRaises(subprocess.CalledProcessError):
            handoff.finish("dev")
        self.passwd.side_effect = None
        self.assertTrue(handoff.finish("dev"))

    def test_interruption_after_lock_before_cleanup_can_finish(self):
        handoff.record("root", "dev")
        with patch.object(Path, "unlink", side_effect=OSError("interrupted")):
            with self.assertRaises(OSError):
                handoff.finish("dev")
        self.assertTrue(handoff.finish("dev"))
        self.assertFalse(handoff.finish("dev"))
        self.assertEqual(self.passwd.call_count, 2)
