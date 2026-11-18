"""Regression tests for candidate substitution and effective SSH policy."""

import importlib.util
from pathlib import Path
import shlex
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

    def stage(self):
        staging = self.root / "staging"
        staging.mkdir()
        return validator.stage_config(self.main, self.target, self.candidate.read_text(), self.root, staging)

    @staticmethod
    def children(staged):
        return [Path(shlex.split(line)[1]) for line in staged.read_text().splitlines() if line.startswith("Include ")]

    def test_new_dropin_participates_in_sorted_include(self):
        provider = self.dropins / "50-provider.conf"
        provider.write_text("PasswordAuthentication yes\n")
        staged, found = self.stage()
        self.assertTrue(found)
        self.assertEqual([child.read_text() for child in self.children(staged)],
                         [self.candidate.read_text(), provider.read_text()])
        self.assertFalse(self.target.exists())

    def test_replacement_does_not_modify_live_file(self):
        self.target.write_text("PasswordAuthentication yes\n")
        staged, found = self.stage()
        self.assertTrue(found)
        self.assertEqual(self.children(staged)[0].read_text(), self.candidate.read_text())
        self.assertEqual(self.target.read_text(), "PasswordAuthentication yes\n")

    def test_recursive_include_is_rejected(self):
        (self.dropins / "recursive.conf").write_text("Include sshd_config\n")
        with self.assertRaisesRegex(ValueError, "Recursive"):
            self.stage()

    def test_match_scope_and_nested_includes_are_preserved(self):
        self.main.write_text("Match User other\nInclude nested\nMatch all\n")
        (self.root / "nested").write_text("Include sshd_config.d/*.conf\n")
        staged, found = self.stage()
        self.assertTrue(found)
        self.assertTrue(staged.read_text().startswith("Match User other\nInclude "))
        self.assertTrue(staged.read_text().endswith("Match all\n"))
        nested, = self.children(staged)
        candidate, = self.children(nested)
        self.assertEqual(candidate.read_text(), self.candidate.read_text())

    def test_parent_glob_cannot_include_a_new_file_in_a_subdirectory(self):
        self.main.write_text("Include *.conf\n")
        with self.assertRaisesRegex(ValueError, "does not include"):
            validator.validate(self.candidate, self.target, ["no"] * 3, self.main)

    def test_wildcard_directory_can_include_a_new_dropin(self):
        self.main.write_text("Include sshd_*.d/*.conf\n")
        _, found = self.stage()
        self.assertTrue(found)

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
