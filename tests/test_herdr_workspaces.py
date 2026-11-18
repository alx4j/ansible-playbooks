"""Exercise Herdr failure handling and credential isolation without a server."""

import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from test_work_repositories import SYNC


class HerdrWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.home = Path(self.temporary.name)
        self.path = self.home / "projects/api"
        self.herdr = SYNC.Herdr(self.home)

    def test_empty_selection_does_not_start_or_contact_herdr(self):
        with patch.object(self.herdr, "command", side_effect=AssertionError("Unexpected command")):
            self.assertEqual(self.herdr.reconcile([]),
                             {"session": "default", "started": False, "created": [], "kept": []})

    def test_existing_workspace_is_only_read(self):
        listing = {"result": {"worktrees": [{"path": str(self.path), "is_bare": False, "open_workspace_id": "w7"}]}}
        with patch.object(self.herdr, "start", return_value=False), \
                patch.object(self.herdr, "command", return_value=listing) as command:
            report = self.herdr.reconcile([("fixture/api", self.path)])
        self.assertEqual(report["kept"], ["fixture/api"])
        self.assertEqual(report["created"], [])
        command.assert_called_once_with("--session", "default", "worktree", "list", "--cwd", self.path)

    def test_concurrent_open_is_reused_without_changing_its_label_or_focus(self):
        listing = {"result": {"worktrees": [{"path": str(self.path), "is_bare": False}]}}
        for already_open in (False, True):
            with self.subTest(already_open=already_open), patch.object(self.herdr, "start", return_value=False), \
                    patch.object(self.herdr, "command", side_effect=[listing, {"result": {"already_open": already_open}}]) as command:
                report = self.herdr.reconcile([("fixture/api", self.path)])
            self.assertEqual(report["kept" if already_open else "created"], ["fixture/api"])
            command.assert_called_with("--session", "default", "worktree", "open",
                                       "--cwd", self.path, "--path", self.path, "--no-focus")

    def test_missing_checkout_or_invalid_listing_never_creates_a_workspace(self):
        for listing in ({}, {"result": {"worktrees": []}},
                        {"result": {"worktrees": [{"path": str(self.path), "is_bare": True}]}}):
            with self.subTest(listing=listing), patch.object(self.herdr, "start", return_value=False), \
                    patch.object(self.herdr, "command", return_value=listing) as command:
                with self.assertRaises(SYNC.SyncError):
                    self.herdr.reconcile([("fixture/api", self.path)])
                self.assertEqual(command.call_count, 1)

    def test_api_credentials_and_caller_session_are_not_inherited(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "fixture-only", "DOPPLER_TOKEN": "fixture-only",
                                     "HERDR_SOCKET": "/unrelated/session.sock", "HERDR_SESSION": "unrelated"}):
            herdr = SYNC.Herdr(self.home)
        result = subprocess.CompletedProcess([], 0, stdout='{"sessions": []}')
        with patch.object(SYNC.subprocess, "run", return_value=result) as run:
            self.assertFalse(herdr.running())
        self.assertEqual(run.call_args.kwargs["env"], herdr.environment)
        for name in ("GITHUB_TOKEN", "DOPPLER_TOKEN", "HERDR_SOCKET", "HERDR_SESSION"):
            self.assertNotIn(name, herdr.environment)

    def test_command_errors_do_not_emit_raw_diagnostics(self):
        error = subprocess.CalledProcessError(1, "herdr", output="private output", stderr="private diagnostics")
        with patch.object(SYNC.subprocess, "run", side_effect=error):
            with self.assertRaises(SYNC.SyncError) as result:
                self.herdr.running()
        self.assertNotIn("private", str(result.exception))

    def test_running_session_is_not_restarted(self):
        with patch.object(self.herdr, "running", return_value=True), \
                patch.object(SYNC.subprocess, "Popen", side_effect=AssertionError("Unexpected restart")):
            self.assertFalse(self.herdr.start())

    def test_startup_failure_is_reported_with_a_clean_child_environment(self):
        with patch.object(self.herdr, "running", return_value=False), \
                patch.object(SYNC.subprocess, "Popen") as launch:
            launch.return_value.poll.return_value = 1
            with self.assertRaises(SYNC.SyncError):
                self.herdr.start()
        self.assertEqual(launch.call_args.kwargs["env"], self.herdr.environment)
        self.assertTrue(launch.call_args.kwargs["start_new_session"])


if __name__ == "__main__":
    unittest.main()
