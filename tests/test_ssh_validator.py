"""Regression tests for candidate substitution and effective SSH policy."""

import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "roles/dev_host/files/validate_ssh.py"
SPEC = importlib.util.spec_from_file_location("validate_ssh", MODULE)
validator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(validator)


class CandidateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dropins = self.root / "sshd_config.d"
        self.dropins.mkdir()
        self.main = self.root / "sshd_config"
        self.main.write_text("Include sshd_config.d/*.conf\nUsePAM yes\n")
        self.target = self.dropins / "00-hardening.conf"
        self.candidate = self.root / "candidate"
        self.candidate.write_text("PermitRootLogin no\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n")

    def expand(self):
        return validator.expand_config(self.main, self.target, self.candidate.read_text(), self.root)

    def test_new_dropin_participates_in_sorted_include(self):
        (self.dropins / "50-provider.conf").write_text("PasswordAuthentication yes\n")
        content, found = self.expand()
        self.assertTrue(found)
        self.assertLess(content.index("PasswordAuthentication no"), content.index("PasswordAuthentication yes"))
        self.assertFalse(self.target.exists())

    def test_replacement_does_not_modify_live_file(self):
        self.target.write_text("PasswordAuthentication yes\n")
        content, found = self.expand()
        self.assertTrue(found)
        self.assertNotIn("PasswordAuthentication yes", content)
        self.assertEqual(self.target.read_text(), "PasswordAuthentication yes\n")

    def test_recursive_include_is_rejected(self):
        (self.dropins / "recursive.conf").write_text("Include sshd_config\n")
        with self.assertRaisesRegex(ValueError, "Recursive"):
            self.expand()

    def test_match_scope_and_nested_includes_are_preserved(self):
        self.main.write_text("Match User other\nInclude sshd_config.d/*.conf\nMatch all\n")
        content, _ = self.expand()
        self.assertTrue(content.startswith("Match User other\nPermitRootLogin no\n"))
        self.assertTrue(content.endswith("Match all\n"))

    def test_missing_include_is_rejected(self):
        self.main.write_text("PasswordAuthentication yes\n")
        with self.assertRaisesRegex(ValueError, "does not include"):
            validator.validate(self.candidate, self.target, ["no"] * 3, self.main)

    def test_effective_policy_conflict_is_rejected(self):
        responses = [
            subprocess.CompletedProcess([], 0, "", ""),
            subprocess.CompletedProcess([], 0, "permitrootlogin yes\npasswordauthentication no\nkbdinteractiveauthentication no\n", ""),
        ]
        with patch.object(validator.subprocess, "run", side_effect=responses):
            with self.assertRaisesRegex(ValueError, "Effective permitrootlogin"):
                validator.validate(self.candidate, self.target, ["no"] * 3, self.main)

    def test_temporary_configuration_is_removed_after_validation_failure(self):
        captured = []

        def fail(argv, **kwargs):
            captured.append(Path(argv[-1]))
            raise subprocess.CalledProcessError(1, argv)

        with patch.object(validator.subprocess, "run", side_effect=fail):
            with self.assertRaises(subprocess.CalledProcessError):
                validator.validate(self.candidate, self.target, ["no"] * 3, self.main)
        self.assertTrue(captured)
        self.assertFalse(captured[0].exists())


if __name__ == "__main__":
    unittest.main()
