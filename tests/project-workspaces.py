"""Verify repository synchronization against real Herdr in a disposable account."""

import argparse
from functools import partial
import json
import os
from pathlib import Path
import pwd
import socket
import subprocess
import sys
import tempfile
import time
import uuid

from test_work_repositories import FakeGitHub, LocalGit, SYNC, legacy_archive, repository


def main(projects, channel):
    assert pwd.getpwuid(os.getuid()).pw_name == "ansibletest", "Run only as the disposable test account"
    home = Path.home()
    session = "ansible-projects-" + uuid.uuid4().hex[:8]
    herdr = SYNC.Herdr(home, session)
    state = home / ".local/state/work-repositories"
    state.mkdir(parents=True, exist_ok=True)

    def command(*args):
        result = subprocess.run([str(herdr.binary), "--session", session, *args], cwd=home,
                                env=herdr.environment, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)["result"] if result.stdout.strip() else None

    def layout():
        snapshot = command("api", "snapshot")["snapshot"]
        return {"focus": snapshot["focused_workspace_id"],
                "workspaces": [(w["workspace_id"], w["label"], w["tab_count"], w["pane_count"])
                               for w in snapshot["workspaces"]],
                "tabs": [(t["tab_id"], t["label"]) for t in snapshot["tabs"]]}

    assert SYNC.activity.query(channel, home) == SYNC.activity.UNKNOWN
    assert SYNC.activity.query(channel, projects / "../elsewhere") == SYNC.activity.UNKNOWN
    activity = partial(SYNC.busy_reason, channel=channel)
    with tempfile.TemporaryDirectory(prefix="herdr-upstream-", dir=home) as upstream:
        subprocess.run(["git", "init", "-q", "-b", "main", upstream], check=True)
        (Path(upstream) / "tracked").write_text("fixture\n")
        git = LocalGit(home, Path(upstream))
        git.run("-C", upstream, "add", ".")
        git.run("-C", upstream, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "initial")
        api = FakeGitHub([repository()])

        def sync(with_herdr=True):
            return SYNC.Reconciler(home, projects, "fixture", "work", api, git,
                                   activity=activity, herdr=herdr if with_herdr else None).run()

        try:
            # Backfill a checkout created before workspace synchronization was enabled.
            assert sync(with_herdr=False)["cloned"] == ["fixture/one"]
            first = sync()
            assert first["herdr"]["started"] and first["herdr"]["created"] == ["fixture/one"], first
            workspace = command("workspace", "list")["workspaces"][0]["workspace_id"]
            pane = command("pane", "list")["panes"][0]["pane_id"]
            command("workspace", "rename", workspace, "My API work")
            command("tab", "create", "--workspace", workspace, "--label", "logs", "--no-focus")
            command("pane", "split", pane, "--direction", "right", "--no-focus")
            for item in command("pane", "list")["panes"]:
                command("pane", "run", item["pane_id"], 'cd "$HOME"')
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                if all(item.get("foreground_cwd") == str(home) for item in command("pane", "list")["panes"]):
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("Fixture shells did not change directory")

            # Reuse a manually created project workspace, and preserve unrelated layouts.
            api.repositories.append(repository(2, "two"))
            assert sync(with_herdr=False)["cloned"] == ["fixture/two"]
            command("workspace", "create", "--cwd", str(projects / "two"), "--label", "Manual workspace", "--no-focus")
            command("workspace", "create", "--cwd", str(home), "--label", "Personal shell", "--focus")
            before = layout()
            config = (projects / "one/.git/config").read_bytes()
            api.repositories.append(repository(3, "three"))
            created = sync()
            assert created["herdr"]["created"] == ["fixture/three"], created
            assert created["herdr"]["kept"] == ["fixture/one", "fixture/two"], created
            after = layout()
            assert after["focus"] == before["focus"]
            assert after["workspaces"][:-1] == before["workspaces"]
            assert after["tabs"][:-1] == before["tabs"]
            assert (projects / "one/.git/config").read_bytes() == config
            assert not (projects / "one/.git/worktrees").exists(), "Opening a checkout created a Git worktree"
            assert not sync()["changed"]
            assert layout() == after

            # Restored Herdr state retains the checkout identity even after cd and rename.
            command("server", "stop")
            restarted = sync()
            assert restarted["herdr"]["started"] and not restarted["herdr"]["created"], restarted
            assert layout() == after
            assert not sync()["changed"]

            # Removing the topic preserves the checkout and its open workspace.
            api.repositories[0]["topics"] = []
            busy = sync()
            assert not busy["changed"] and not busy["archived"], busy
            assert layout() == after
            assert "fixture/one" not in busy["herdr"]["kept"]
            command("workspace", "close", workspace)
            deadline = time.monotonic() + 10
            while reason := activity(projects / "one", home):
                assert time.monotonic() < deadline, f"Closed fixture workspace remained active: {reason}"
                time.sleep(0.1)
            assert not sync()["changed"]
            assert (projects / "one/tracked").is_file()
            # A legacy archive still needs the privileged activity check before a restore.
            archive = legacy_archive(projects, state / (projects.name + ".json"))
            api.repositories[0]["topics"] = ["work"]
            holder_code = """import ctypes, sys
assert ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE
with open(sys.argv[1]) as tracked:
    print('holding', flush=True)
    sys.stdin.readline()
print('released', flush=True)
sys.stdin.readline()
"""
            with subprocess.Popen([sys.executable, "-c", holder_code, str(archive / "tracked")],
                                  cwd=home, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as holder:
                try:
                    assert holder.stdout.readline().strip() == "holding"
                    assert SYNC.activity.scan(archive, os.getuid()) == SYNC.activity.UNKNOWN
                    held = sync()
                    assert held["skipped"] == [{"repository": "fixture/one", "reason": SYNC.activity.FILES_OPEN}], held
                    holder.stdin.write("release\n")
                    holder.stdin.flush()
                    assert holder.stdout.readline().strip() == "released"
                    restored = sync()
                finally:
                    holder.stdin.close()
            assert restored["restored"] == ["fixture/one"] and restored["herdr"]["created"] == ["fixture/one"], restored
            assert (projects / "one/tracked").read_text() == "fixture\n"
            assert not sync()["changed"]

            # Git repositories without a first commit can also have a workspace.
            empty = projects / "empty"
            git.run("init", "-q", empty)
            assert herdr.reconcile([("fixture/empty", empty)])["created"] == ["fixture/empty"]
            assert herdr.reconcile([("fixture/empty", empty)])["kept"] == ["fixture/empty"]
        finally:
            if herdr.running():
                command("server", "stop")
            (state / (projects.name + ".json")).unlink(missing_ok=True)
            (state / (projects.name + ".lock")).unlink(missing_ok=True)

    print("Herdr startup, backfill, layout preservation, restart, topic removal, legacy restore, and zero-change reruns passed")


if __name__ == "__main__":
    if os.getuid() == 0:
        account = pwd.getpwnam("ansibletest")
        with tempfile.TemporaryDirectory(prefix="herdr-projects-", dir=account.pw_dir) as directory:
            os.chown(directory, account.pw_uid, account.pw_gid)
            subprocess.run([sys.executable, "-I", SYNC.activity.__file__, account.pw_name, directory,
                            sys.executable, str(Path(__file__).resolve())], check=True)
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--projects", type=Path, required=True)
        parser.add_argument("--activity-fd", type=int, required=True)
        args = parser.parse_args()
        with socket.socket(fileno=args.activity_fd) as channel:
            channel.set_inheritable(False)
            main(args.projects, channel)
