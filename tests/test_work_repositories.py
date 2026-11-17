"""Exercise reconciliation with real Git data and synthetic GitHub responses."""

import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import urllib.request


SOURCE = Path(__file__).resolve().parents[1] / "roles/work_repositories/files/sync.py"
sys.path.insert(0, str(SOURCE.parents[2] / "github_access/files"))
import github_access
spec = importlib.util.spec_from_file_location("work_sync", SOURCE)
SYNC = importlib.util.module_from_spec(spec)
spec.loader.exec_module(SYNC)


def repository(ident=1, name="one", topics=None):
    return {"id": ident, "name": name, "owner": {"login": "fixture"},
            "topics": ["work"] if topics is None else topics}


class FakeGitHub:
    def __init__(self, repositories):
        self.repositories = repositories
        self.listed = None
        self.lookup = None

    def request(self, path, payload=None):
        if path == "/user":
            return {"login": "fixture"}
        if self.lookup:
            return self.lookup(path)
        for repo in self.repositories:
            if path == f"/repos/{repo['owner']['login']}/{repo['name']}":
                return copy.deepcopy(repo)
        raise SYNC.ApiError(404)

    def pages(self, path):
        return iter(copy.deepcopy(self.repositories if self.listed is None else self.listed))


class LocalGit(SYNC.Git):
    """Only the transport is local; checkout, worktree, and ownership checks are real Git."""
    def __init__(self, home, upstream):
        super().__init__(home)
        self.upstream = upstream

    def enroll(self, api, owner):
        return False

    def clone(self, repo, destination):
        self.run("clone", self.upstream, destination)
        self.run("-C", destination, "remote", "set-url", "origin", f"git@github.com:{repo['repo']}.git")


class WorkRepositoriesTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="work-repositories-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.home = self.root / "home"
        self.projects = self.home / "projects"
        self.projects.mkdir(parents=True)
        (self.home / ".local/state/work-repositories").mkdir(parents=True)
        self.upstream = self.root / "upstream"
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.upstream)], check=True)
        (self.upstream / "tracked").write_text("original\n")
        (self.upstream / ".gitignore").write_text(".env\n")
        self.git = LocalGit(self.home, self.upstream)
        self.git.run("-C", self.upstream, "add", ".")
        self.commit(self.upstream, "initial")
        self.api = FakeGitHub([repository()])

    def commit(self, path, message):
        self.git.run("-C", path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "-qm", message)

    def sync(self, activity=lambda *_: None):
        return SYNC.Reconciler(self.home, self.projects, "fixture", "work", self.api, self.git, activity).run()

    def manifest(self):
        return self.home / ".local/state/work-repositories/projects.json"

    def record(self):
        return json.loads(self.manifest().read_text())["repositories"]["1"]

    def test_new_repositories_clone_existing_work_is_untouched(self):
        self.assertEqual(self.sync()["cloned"], ["fixture/one"])
        path = self.projects / "one"
        self.git.run("-C", path, "switch", "-c", "my-work")
        (path / "tracked").write_text("unfinished\n")
        before = (path / ".git/config").read_bytes()
        self.assertFalse(self.sync()["changed"])
        self.api.repositories.append(repository(2, "two"))
        self.assertEqual(self.sync()["cloned"], ["fixture/two"])
        self.assertEqual(self.git.run("-C", path, "branch", "--show-current"), "my-work")
        self.assertEqual((path / "tracked").read_text(), "unfinished\n")
        self.assertEqual((path / ".git/config").read_bytes(), before)
        self.assertFalse((path / ".git/FETCH_HEAD").exists())

    def test_archive_preserves_unpushed_work_ignored_files_stashes_and_restores(self):
        self.sync()
        path = self.projects / "one"
        self.git.run("-C", path, "switch", "-c", "unpublished")
        (path / "tracked").write_text("unpublished commit\n")
        self.git.run("-C", path, "add", ".")
        self.commit(path, "unpublished")
        (path / "tracked").write_text("stashed work\n")
        self.git.run("-C", path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "stash", "push", "-m", "keep")
        (path / "tracked").write_text("unfinished\n")
        (path / "untracked").write_text("local file\n")
        (path / ".env").write_text("fixture-only\n")
        refs = self.git.run("-C", path, "show-ref")
        self.api.repositories[0]["topics"] = []
        self.assertEqual(self.sync()["archived"], ["fixture/one"])
        archived = self.projects / self.record()["path"]
        self.assertFalse(path.exists())
        self.assertEqual(self.git.run("-C", archived, "show-ref"), refs)
        self.assertEqual((archived / ".env").read_text(), "fixture-only\n")
        self.assertEqual((archived / "tracked").read_text(), "unfinished\n")
        self.assertFalse(self.sync()["changed"])
        self.api.repositories[0]["topics"] = ["work"]
        self.assertEqual(self.sync()["restored"], ["fixture/one"])
        self.assertEqual(self.git.run("-C", path, "show-ref"), refs)
        self.assertEqual((path / "untracked").read_text(), "local file\n")
        self.assertFalse(self.sync()["changed"])

    def test_existing_unmanaged_directory_is_never_adopted(self):
        path = self.projects / "one"
        path.mkdir()
        (path / "keep").write_text("personal\n")
        result = self.sync()
        self.assertFalse(result["changed"])
        self.assertTrue(result["skipped"])
        self.api.repositories[0]["topics"] = []
        self.sync()
        self.assertEqual((path / "keep").read_text(), "personal\n")

    def test_access_loss_and_stale_listing_never_archive(self):
        self.sync()
        self.api.listed = []
        self.assertIn("topic still exists", self.sync()["skipped"][0]["reason"])
        self.api.repositories = []
        result = self.sync()
        self.assertFalse(result["changed"])
        self.assertIn("inaccessible", result["skipped"][0]["reason"])
        self.assertTrue((self.projects / "one").exists())

    def test_listing_failure_or_duplicate_ids_prevent_all_changes(self):
        self.sync()
        self.api.repositories = [repository(2, "two"), repository(2, "two")]
        with self.assertRaises(SYNC.SyncError):
            self.sync()
        self.assertFalse((self.projects / "two").exists())
        def partial(_):
            yield repository(2, "two")
            raise SYNC.ApiError(403)
        with patch.object(self.api, "pages", side_effect=partial):
            with self.assertRaises(SYNC.ApiError):
                self.sync()
        self.assertTrue((self.projects / "one").exists())
        self.assertFalse((self.projects / "two").exists())

    def test_rename_keeps_checkout_and_new_repository_cannot_take_its_path(self):
        self.sync()
        self.api.repositories[0]["name"] = "renamed"
        result = self.sync()
        self.assertEqual(result["kept"], ["fixture/renamed"])
        self.assertTrue((self.projects / "one").exists())
        self.assertFalse((self.projects / "renamed").exists())
        self.api.repositories.append(repository(2, "one"))
        self.assertTrue(self.sync()["skipped"])

    def test_positive_lookup_must_match_id_and_owner(self):
        self.sync()
        self.api.listed = []
        self.api.lookup = lambda _: repository(99, "one", [])
        self.assertFalse(self.sync()["changed"])
        self.api.lookup = lambda _: {**repository(1, "one", []), "owner": {"login": "another-owner"}}
        self.assertFalse(self.sync()["changed"])
        self.assertTrue((self.projects / "one").exists())

    def test_busy_and_linked_worktrees_prevent_archive(self):
        self.sync()
        self.api.repositories[0]["topics"] = []
        result = self.sync(activity=lambda *_: "active workspace")
        self.assertFalse(result["changed"])
        self.assertFalse((self.projects / ".archive").exists())
        self.git.run("-C", self.projects / "one", "worktree", "add", "-b", "linked", self.root / "linked")
        self.assertIn("linked Git worktrees", self.sync()["skipped"][0]["reason"])
        self.assertTrue((self.projects / "one").exists())

    def test_actual_process_cwd_and_open_file_prevent_archive(self):
        self.sync()
        path = self.projects / "one"
        process = subprocess.Popen(["sleep", "60"], cwd=path)
        try:
            self.assertIn("working in", SYNC.busy_reason(path, self.home))
        finally:
            process.terminate()
            process.wait()
        with (path / "tracked").open():
            self.assertIn("files open", SYNC.busy_reason(path, self.home))

    def test_origin_change_and_symlinks_preserve_everything(self):
        self.sync()
        path = self.projects / "one"
        self.git.run("-C", path, "remote", "set-url", "origin", "git@github.com:fixture/different.git")
        self.api.repositories[0]["topics"] = []
        self.assertFalse(self.sync()["changed"])
        moved = self.root / "outside"
        path.rename(moved)
        path.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(SYNC.SyncError):
            self.sync()
        self.assertTrue((moved / "tracked").exists())

    def test_move_recovery_before_and_after_rename(self):
        for complete_move in (False, True):
            with self.subTest(complete_move=complete_move):
                self.sync()
                self.api.repositories[0]["topics"] = []
                data = json.loads(self.manifest().read_text())
                record = data["repositories"]["1"]
                record["pending"] = {"path": ".archive/recovery", "state": "archived"}
                (self.projects / ".archive").mkdir(exist_ok=True)
                if complete_move:
                    SYNC.rename_exclusive(self.projects / record["path"], self.projects / ".archive/recovery")
                self.manifest().write_text(json.dumps(data))
                recovered = self.sync()
                self.assertEqual(recovered["skipped"], [])
                self.assertEqual(self.record()["state"], "archived")
                self.assertNotIn("pending", self.record())
                if complete_move:
                    self.assertEqual(self.record()["path"], ".archive/recovery")
                    self.assertEqual(recovered["archived"], [])
                self.api.repositories[0]["topics"] = ["work"]
                self.sync()

    def test_interrupted_clone_recovers_without_recloning(self):
        self.sync()
        data = json.loads(self.manifest().read_text())
        data["repositories"]["1"]["state"] = "cloning"
        self.manifest().write_text(json.dumps(data))
        with patch.object(self.git, "clone", side_effect=AssertionError("Unexpected clone")):
            self.sync()
        self.assertEqual(self.record()["state"], "active")

    def test_failed_clone_leaves_no_partial_destination_and_can_retry(self):
        with patch.object(self.git, "clone", side_effect=SYNC.SyncError("fixture failure")):
            with self.assertRaises(SYNC.SyncError):
                self.sync()
        self.assertFalse((self.projects / "one").exists())
        self.assertEqual(self.sync()["cloned"], ["fixture/one"])

    def test_atomic_move_never_overwrites_even_an_empty_destination(self):
        source, destination = self.root / "source", self.root / "destination"
        source.mkdir()
        destination.mkdir()
        with self.assertRaises(OSError):
            SYNC.rename_exclusive(source, destination)
        self.assertTrue(source.is_dir() and destination.is_dir())

    def test_herdr_activity_and_probe_failure_prevent_archive(self):
        herdr = self.home / ".local/bin/herdr"
        herdr.parent.mkdir(parents=True)
        herdr.touch()
        path = self.projects / "one"
        replies = [json.dumps({"sessions": [{"name": "work", "running": True}]}),
                   json.dumps({"result": {"workspaces": [{"worktree": None}]}}),
                   json.dumps({"result": {"panes": [{"foreground_cwd": str(path)}]}})]
        with patch.object(SYNC, "process_activity", return_value=None), patch.object(SYNC.subprocess, "check_output", side_effect=replies):
            self.assertIn("Herdr", SYNC.busy_reason(path, self.home))
        with patch.object(SYNC, "process_activity", return_value=None), patch.object(SYNC.subprocess, "check_output", side_effect=OSError()):
            self.assertIn("cannot verify", SYNC.busy_reason(path, self.home))

    def test_api_pagination_and_cross_origin_redirect_guard(self):
        api = SYNC.GitHub("synthetic-token")
        with patch.object(api, "request", side_effect=[[repository(i + 1, f"r{i}") for i in range(100)], []]) as request:
            self.assertEqual(len(list(api.pages("/user/repos?affiliation=owner"))), 100)
            self.assertIn("page=2", request.call_args.args[0])
        redirect = github_access.GitHubRedirect()
        with self.assertRaises(SYNC.SyncError):
            redirect.redirect_request(urllib.request.Request("https://api.github.com/repos/fixture/one"),
                                      None, 301, "Moved", {}, "https://example.invalid/steal")

    def test_child_processes_never_receive_api_credentials(self):
        with patch.dict(os.environ, {"GITHUB_TOKEN": "synthetic-token", "DOPPLER_TOKEN": "synthetic-bootstrap"}):
            environment = self.git.process(["/usr/bin/env"]).stdout
        self.assertNotIn("synthetic", environment)
        self.assertNotIn("TOKEN", environment)

    def test_key_registration_is_only_needed_until_ssh_works(self):
        git = SYNC.Git(self.home)
        public = subprocess.CompletedProcess([], 0, stdout="ssh-ed25519 AAAAfixture\n", stderr="")
        with patch.object(git, "authenticate", side_effect=[False, False, True]), \
                patch.object(git, "ensure_key", return_value=False), \
                patch.object(git, "process", return_value=public), \
                patch.object(self.api, "pages", return_value=iter([])), \
                patch.object(self.api, "request", return_value={}) as request:
            self.assertTrue(git.enroll(self.api, "fixture"))
            self.assertEqual(request.call_args.args[0], "/user/keys")
            self.assertEqual(request.call_args.args[1]["key"], "ssh-ed25519 AAAAfixture")
        with patch.object(git, "authenticate", return_value=True), \
                patch.object(git, "ensure_key", side_effect=AssertionError("Unexpected new SSH identity")), \
                patch.object(self.api, "request", side_effect=AssertionError("Unexpected API key access")), \
                patch.object(self.api, "pages", side_effect=AssertionError("Unexpected key listing")):
            self.assertFalse(git.enroll(self.api, "fixture"))

    def test_key_permission_failure_is_actionable_and_does_not_change_projects(self):
        git = SYNC.Git(self.home)
        public = subprocess.CompletedProcess([], 0, stdout="ssh-ed25519 AAAAfixture\n", stderr="")
        with patch.object(git, "authenticate", return_value=False), \
                patch.object(git, "ensure_key", return_value=False), \
                patch.object(git, "process", return_value=public), \
                patch.object(self.api, "pages", side_effect=SYNC.ApiError(403)):
            with self.assertRaisesRegex(SYNC.SyncError, "Git SSH keys write"):
                git.enroll(self.api, "fixture")
        self.assertEqual(list(self.projects.iterdir()), [])

    def test_removal_lookup_failure_aborts_before_cloning_new_repositories(self):
        self.sync()
        self.api.repositories = [repository(2, "two")]
        def forbidden(_):
            raise SYNC.ApiError(403)
        self.api.lookup = forbidden
        with self.assertRaises(SYNC.ApiError):
            self.sync()
        self.assertFalse((self.projects / "two").exists())
        self.assertTrue((self.projects / "one").exists())

    def test_concurrent_run_cannot_change_repositories(self):
        lock = self.home / ".local/state/work-repositories/projects.lock"
        with lock.open("w") as stream:
            SYNC.fcntl.flock(stream, SYNC.fcntl.LOCK_EX | SYNC.fcntl.LOCK_NB)
            with self.assertRaisesRegex(SYNC.SyncError, "already running"):
                self.sync()
        self.assertFalse((self.projects / "one").exists())


if __name__ == "__main__":
    unittest.main()
