#!/usr/bin/env python3
"""Reconcile GitHub work topics with owned SSH checkouts; never delete projects."""

import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import uuid


# The installed shared module is root-owned; -I excludes user Python paths.
sys.path.insert(0, "/usr/local/lib/github-access")
from github_access import ApiError, GitHub, Git as AccessGit, SyncError


def repo_info(value):
    try:
        ident, name, owner, topics = value["id"], value["name"], value["owner"]["login"], value["topics"]
        valid = (type(ident) is int and ident > 0 and isinstance(name, str)
                 and re.fullmatch(r"[A-Za-z0-9_.-]+", name) and name not in (".", "..", ".archive")
                 and isinstance(owner, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", owner)
                 and isinstance(topics, list) and all(isinstance(topic, str) for topic in topics))
        if not valid:
            raise ValueError
        return {"id": str(ident), "name": name, "repo": f"{owner}/{name}", "owner": owner, "topics": topics}
    except (KeyError, TypeError, ValueError):
        raise SyncError("GitHub returned incomplete or unsafe repository metadata.") from None


def discover(api, owner, topic):
    if api.request("/user").get("login", "").lower() != owner.lower():
        raise SyncError("The Doppler GitHub token must belong to the configured repository owner.")
    desired, seen = {}, set()
    for raw in api.pages("/user/repos?affiliation=owner&sort=full_name"):
        repo = repo_info(raw)
        if repo["id"] in seen:
            raise SyncError("GitHub pagination returned duplicate repositories; retry a complete listing.")
        seen.add(repo["id"])
        if repo["owner"].lower() == owner.lower() and topic in repo["topics"]:
            desired[repo["id"]] = repo
    return desired


def save_json(path, value):
    # fsync and replace preserve the previous complete manifest after interruption.
    with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            json.dump(value, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)


def rename_exclusive(source, destination):
    # Linux renameat2 refuses even an empty destination created after our checks.
    library = ctypes.CDLL(None, use_errno=True)
    rename = library.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(source), -100, os.fsencode(destination), 1):
        raise OSError(ctypes.get_errno(), "Repository move failed; the destination was not overwritten.")


class Git(AccessGit):
    def owned(self, path, ident, origin):
        if path.is_symlink() or not path.is_dir() or (path / ".git").is_symlink() or not (path / ".git").is_dir():
            return False
        marker = path / ".git/ansible-work-repository.json"
        try:
            return (not marker.is_symlink() and json.loads(marker.read_text()) == {"id": ident}
                    and self.run("-C", path, "config", "--local", "--get", "remote.origin.url") == origin)
        except (OSError, ValueError, SyncError):
            return False

    def linked_worktrees(self, path):
        listing = self.run("-C", path, "worktree", "list", "--porcelain")
        return sum(line.startswith("worktree ") for line in listing.splitlines()) != 1


def inside(path, root):
    try:
        return Path(path).is_relative_to(root)
    except (TypeError, ValueError):
        return False


def process_activity(path):
    inaccessible = False
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if process.stat().st_uid != os.getuid():
                continue
            if inside(os.readlink(process / "cwd"), path):
                return "a process is working in this repository"
            for descriptor in (process / "fd").iterdir():
                try:
                    if inside(os.readlink(descriptor), path):
                        return "a process has files open in this repository"
                except FileNotFoundError:
                    pass
        except (FileNotFoundError, ProcessLookupError):
            continue
        except PermissionError:
            inaccessible = True
    if inaccessible:
        return "cannot inspect a development-account process safely"
    return None


def busy_reason(path, home):
    """Conservative local activity checks, repeated immediately before each move."""
    reason = process_activity(path)
    if reason:
        return reason
    herdr = home / ".local/bin/herdr"
    if not herdr.exists():
        return None
    environment = {"HOME": str(home), "PATH": f"{home}/.local/bin:/usr/bin:/bin"}
    try:
        def command(*args):
            output = subprocess.check_output([str(herdr), *args], env=environment, cwd=home,
                                             stderr=subprocess.DEVNULL, text=True, timeout=15)
            return json.loads(output)
        for session in command("session", "list", "--json")["sessions"]:
            if session["running"]:
                workspaces = command("--session", session["name"], "workspace", "list")["result"]["workspaces"]
                if any(inside((workspace.get("worktree") or {}).get("checkout_path"), path) for workspace in workspaces):
                    return "a running Herdr session has a workspace here"
                panes = command("--session", session["name"], "pane", "list")["result"]["panes"]
                if any(inside(pane.get("cwd"), path) or inside(pane.get("foreground_cwd"), path) for pane in panes):
                    return "a running Herdr session has a pane here"
    except (OSError, ValueError, KeyError, subprocess.SubprocessError):
        return "cannot verify Herdr workspace activity"
    return None


class Reconciler:
    def __init__(self, home, projects, owner, topic, api, git=None, activity=busy_reason):
        self.home, self.projects, self.owner, self.topic, self.api = home, projects, owner, topic, api
        self.git, self.activity = git or Git(home), activity
        if (projects.parent != home or projects.resolve() != projects or not projects.is_dir()
                or not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*", projects.name)):
            raise SyncError("Projects must be an existing real directory directly inside the development home.")
        self.state = home / ".local/state/work-repositories"
        if self.state.resolve() != self.state or not self.state.is_dir():
            raise SyncError("The private repository state directory is missing or is a symlink.")
        self.manifest = self.state / (projects.name + ".json")
        self.report = {"changed": False, "cloned": [], "kept": [], "archived": [], "restored": [], "skipped": []}

    def path(self, relative):
        path = Path(relative)
        valid = (not path.is_absolute() and all(part not in (".", "..") for part in path.parts)
                 and (len(path.parts) == 1 or len(path.parts) == 2 and path.parts[0] == ".archive")
                 and path.name != ".archive")
        absolute = self.projects / path
        if not valid or absolute.resolve() != absolute:
            raise SyncError("A managed repository path is unsafe; nothing at that path was changed.")
        return absolute

    def load(self):
        if self.manifest.is_symlink():
            raise SyncError("Repository manifest must not be a symlink.")
        if not self.manifest.exists():
            return {"version": 1, "owner": self.owner, "repositories": {}}
        value = json.loads(self.manifest.read_text())
        if value.get("version") != 1 or value.get("owner") != self.owner or not isinstance(value.get("repositories"), dict):
            raise SyncError("The repository manifest has an incompatible owner or format.")
        for ident, record in value["repositories"].items():
            if not re.fullmatch(r"[1-9][0-9]*", ident) or record.get("state") not in ("active", "archived", "cloning"):
                raise SyncError("Invalid repository manifest entry.")
            self.path(record["path"])
            if not re.fullmatch(r"[A-Za-z0-9-]+/[A-Za-z0-9_.-]+", record["repo"]):
                raise SyncError("Invalid repository identity in the manifest.")
            if "pending" in record:
                self.path(record["pending"]["path"])
        return value

    def save(self):
        save_json(self.manifest, self.data)
        self.report["changed"] = True

    def skip(self, name, reason):
        self.report["skipped"].append({"repository": name, "reason": reason})

    def recover_moves(self):
        for ident, record in self.records.items():
            if "pending" not in record:
                continue
            pending = record["pending"]
            source, destination = self.path(record["path"]), self.path(pending["path"])
            if not source.exists() and self.git.owned(destination, ident, record["origin"]):
                record.update(pending)
            elif not destination.exists() and self.git.owned(source, ident, record["origin"]):
                pass  # The previous run recorded intent but never moved the directory.
            else:
                raise SyncError("An interrupted repository move needs inspection; both paths were preserved.")
            del record["pending"]
            self.save()

    def move(self, ident, record, relative, state, action):
        source, destination = self.path(record["path"]), self.path(relative)
        if destination.exists():
            self.skip(record["repo"], "destination already exists")
            return
        if self.git.linked_worktrees(source):
            self.skip(record["repo"], "linked Git worktrees need their existing path")
            return
        reason = self.activity(source, self.home)
        if reason:
            self.skip(record["repo"], reason)
            return
        if destination.parent != self.projects:
            destination.parent.mkdir(mode=0o700, exist_ok=True)
        record["pending"] = {"path": relative, "state": state}
        self.save()
        # Rename on the same filesystem retains ignored files, branches, stashes, and uncommitted work.
        rename_exclusive(source, destination)
        record.update(record.pop("pending"))
        self.save()
        self.report[action].append(record["repo"])

    def clone(self, repo):
        ident, name = repo["id"], repo["name"]
        destination = self.path(name)
        if destination.exists():
            self.skip(repo["repo"], "an existing directory is not owned by this automation")
            return
        record = {"repo": repo["repo"], "origin": f"git@github.com:{repo['repo']}.git", "path": name, "state": "cloning"}
        self.records[ident] = record
        self.save()
        with tempfile.TemporaryDirectory(prefix=".work-repo-", dir=self.projects) as temporary:
            checkout = Path(temporary) / "checkout"
            self.git.clone(repo, checkout)
            save_json(checkout / ".git/ansible-work-repository.json", {"id": ident})
            if destination.exists() or destination.is_symlink():
                raise SyncError("A clone destination appeared during synchronization; it was not overwritten.")
            rename_exclusive(checkout, destination)
        record["state"] = "active"
        self.save()
        self.report["cloned"].append(repo["repo"])

    def run(self):
        lock = self.state / (self.projects.name + ".lock")
        with os.fdopen(os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "w") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise SyncError("Another repository synchronization is already running.") from None
            self.data = self.load()
            self.records = self.data["repositories"]
            # Complete discovery and positive removal checks before cloning or moving anything.
            desired = discover(self.api, self.owner, self.topic)
            removed = set()
            for ident, record in self.records.items():
                if ident in desired or record["state"] == "archived":
                    continue
                try:
                    current = repo_info(self.api.request("/repos/" + record["repo"]))
                except ApiError as error:
                    if error.status == 404:
                        self.skip(record["repo"], "repository is inaccessible; topic removal was not confirmed")
                        continue
                    raise
                if current["id"] != ident or current["owner"].lower() != self.owner.lower():
                    self.skip(record["repo"], "repository identity or owner changed")
                elif self.topic not in current["topics"]:
                    removed.add(ident)
                else:
                    self.skip(record["repo"], "topic still exists; repository listing changed during discovery")
            self.recover_moves()
            if desired:
                self.report["changed"] |= self.git.enroll(self.api, self.owner)
            for ident, repo in desired.items():
                record = self.records.get(ident)
                if record is None:
                    self.clone(repo)
                    continue
                path = self.path(record["path"])
                if not path.exists():
                    # A missing active checkout is cloned again; archived data is never silently replaced.
                    if record["state"] in ("active", "cloning"):
                        self.clone(repo)
                    else:
                        self.skip(repo["repo"], "archived checkout is missing; inspect before recreating")
                    continue
                if not self.git.owned(path, ident, record["origin"]):
                    self.skip(repo["repo"], "checkout ownership or origin changed")
                    continue
                if record["repo"] != repo["repo"] or record["state"] == "cloning":
                    record["repo"] = repo["repo"]
                    if record["state"] == "cloning":
                        record["state"] = "active"
                    self.save()
                if record["state"] == "archived":
                    self.move(ident, record, repo["name"], "active", "restored")
                else:
                    self.report["kept"].append(repo["repo"])
            for ident in removed:
                record = self.records[ident]
                if record["state"] == "archived":
                    continue  # Recovery may have completed an earlier archive move.
                path = self.path(record["path"])
                if not self.git.owned(path, ident, record["origin"]):
                    self.skip(record["repo"], "checkout ownership could not be verified")
                    continue
                archive = self.projects / ".archive"
                if archive.is_symlink() or archive.exists() and not archive.is_dir():
                    raise SyncError("The archive path is not a real directory.")
                target = f".archive/{ident}-{path.name}-{uuid.uuid4().hex[:12]}"
                self.move(ident, record, target, "archived", "archived")
            return self.report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--topic", default="work")
    parser.add_argument("--projects", type=Path, required=True)
    args = parser.parse_args()
    token = os.environ.pop("GITHUB_TOKEN", "")
    if not token:
        raise SyncError("Run through the Doppler wrapper with the selected GitHub token.")
    if os.getuid() == 0:
        raise SyncError("Run as the development account, never root.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", args.owner):
        raise SyncError("Invalid GitHub owner.")
    return Reconciler(Path.home(), args.projects, args.owner, args.topic, GitHub(token)).run()


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except SyncError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception:
        print('{"error":"Repository synchronization failed; local work was preserved. No raw diagnostics were emitted."}', file=sys.stderr)
        sys.exit(1)
