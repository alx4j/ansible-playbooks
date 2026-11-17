"""Protect existing source checkouts and keep deployment credentials out of plugins."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("tokeneconomizer_install", ROOT / "roles/tokeneconomizer/files/install.py")
INSTALL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INSTALL)


class TokenEconomizerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.checkout = self.home / "source"

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.checkout), *args], text=True).strip()

    def source(self):
        self.checkout.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Fixture")
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("remote", "add", "origin", INSTALL.REPOSITORY)
        (self.checkout / "source.py").write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "Fixture")
        marker = self.checkout / ".git/ansible-tokeneconomizer.json"
        marker.write_text(json.dumps({"repository": INSTALL.REPOSITORY, "revision": self.git("rev-parse", "HEAD")}))

    def test_absent_and_clean_owned_sources_are_accepted(self):
        INSTALL.check_checkout(self.checkout, self.home)
        self.source()
        INSTALL.check_checkout(self.checkout, self.home)

    def test_unowned_checkout_is_preserved(self):
        self.source()
        (self.checkout / ".git/ansible-tokeneconomizer.json").unlink()
        with self.assertRaisesRegex(INSTALL.InstallError, "not owned"):
            INSTALL.check_checkout(self.checkout, self.home)
        self.assertEqual((self.checkout / "source.py").read_text(), "original\n")

    def test_dirty_and_untracked_files_are_preserved(self):
        self.source()
        for name in ("source.py", "untracked.py"):
            with self.subTest(name=name):
                path = self.checkout / name
                path.write_text("local work\n")
                with self.assertRaisesRegex(INSTALL.InstallError, "local changes"):
                    INSTALL.check_checkout(self.checkout, self.home)
                self.assertEqual(path.read_text(), "local work\n")
                if name == "source.py":
                    path.write_text("original\n")

    def test_local_commits_are_preserved(self):
        self.source()
        (self.checkout / "source.py").write_text("committed work\n")
        self.git("commit", "-qam", "Local work")
        with self.assertRaisesRegex(INSTALL.InstallError, "local changes or commits"):
            INSTALL.check_checkout(self.checkout, self.home)
        self.assertEqual((self.checkout / "source.py").read_text(), "committed work\n")

    def test_changed_origin_is_preserved(self):
        self.source()
        self.git("remote", "set-url", "origin", "https://example.invalid/custom.git")
        with self.assertRaisesRegex(INSTALL.InstallError, "different origin"):
            INSTALL.check_checkout(self.checkout, self.home)

    def test_source_symlink_is_rejected(self):
        other = self.home / "other"
        other.mkdir()
        self.checkout.symlink_to(other, target_is_directory=True)
        with self.assertRaisesRegex(INSTALL.InstallError, "symlink"):
            INSTALL.check_checkout(self.checkout, self.home)

    def test_fingerprint_covers_executable_permissions_and_ignores_bytecode(self):
        self.checkout.mkdir()
        script = self.checkout / "hook"
        script.write_text("#!/bin/sh\n")
        before = INSTALL.fingerprint(self.checkout)
        script.chmod(0o700)
        after = INSTALL.fingerprint(self.checkout)
        self.assertNotEqual(before, after)
        bytecode = self.checkout / "__pycache__"
        bytecode.mkdir()
        (bytecode / "hook.pyc").write_bytes(b"fixture")
        self.assertEqual(after, INSTALL.fingerprint(self.checkout))

    def test_bundle_symlinks_are_rejected(self):
        self.checkout.mkdir()
        (self.checkout / "outside").symlink_to(self.home)
        with self.assertRaisesRegex(INSTALL.InstallError, "symlinks"):
            INSTALL.fingerprint(self.checkout)

    def test_subprocess_does_not_inherit_credentials_or_plugin_overrides(self):
        result = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with patch.dict(os.environ, {"GITHUB_TOKEN": "synthetic", "DOPPLER_TOKEN": "synthetic", "TE_CORE_PATH": "/unexpected"}):
            with patch.object(INSTALL.subprocess, "run", return_value=result) as call:
                self.assertEqual(INSTALL.run(["tool"], self.home), "ok")
        env = call.call_args.kwargs["env"]
        self.assertFalse({"GITHUB_TOKEN", "DOPPLER_TOKEN", "TE_CORE_PATH"} & env.keys())
        self.assertEqual(env["CODEX_HOME"], str(self.home / ".codex"))

    def test_failed_command_diagnostics_are_not_repeated(self):
        result = subprocess.CompletedProcess([], 1, stdout="private fixture", stderr="private fixture")
        with patch.object(INSTALL.subprocess, "run", return_value=result):
            with self.assertRaises(INSTALL.InstallError) as caught:
                INSTALL.run(["tool"], self.home)
        self.assertNotIn("private fixture", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
