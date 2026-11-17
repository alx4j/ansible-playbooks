#!/usr/bin/env python3
"""Reconcile GitHub work topics with owned SSH checkouts; never delete projects."""

import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid


class SyncError(Exception):
    pass


class ApiError(SyncError):
    def __init__(self, status):
        self.status = status
        super().__init__(f"GitHub API returned HTTP {status}; check token access. No response body was logged.")


class GitHubRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urllib.parse.urlsplit(newurl)
        if target.scheme != "https" or target.netloc != "api.github.com":
            raise SyncError("Refused a GitHub API redirect to another origin.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class GitHub:
    def __init__(self, token):
        self.token = token
        self.opener = urllib.request.build_opener(GitHubRedirect)

    def request(self, path, payload=None):
        if not path.startswith("/") or path.startswith("//"):
            raise SyncError("Invalid GitHub API path.")
        request = urllib.request.Request(
            "https://api.github.com" + path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={"Authorization": "Bearer " + self.token,
                     "Accept": "application/vnd.github+json",
                     "Content-Type": "application/json", "X-GitHub-Api-Version": "2026-03-10"},
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise ApiError(error.code) from None
        except (OSError, ValueError):
            raise SyncError("GitHub request failed; no repository changes were inferred from that failure.") from None

    def pages(self, path):
        separator = "&" if "?" in path else "?"
        for page in range(1, 1001):
            result = self.request(f"{path}{separator}per_page=100&page={page}")
            if not isinstance(result, list):
                raise SyncError("GitHub returned an invalid repository or key list.")
            yield from result
            if len(result) < 100:
                return
        raise SyncError("GitHub pagination did not finish; synchronization stopped.")


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


class Git:
    def __init__(self, home, ssh_options=None):
        self.home = home
        self.key = home / ".ssh/id_ed25519_github_work"
        self.ssh = ["/usr/bin/ssh",
                    "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=15", "-o", "HostKeyAlgorithms=ssh-ed25519",
                    "-o", "GlobalKnownHostsFile=/dev/null"]
        self.ssh += ssh_options or ["-o", "UserKnownHostsFile=/usr/local/lib/work-repositories/github_known_hosts"]
        # Never pass the API token, Doppler token, or agent environment to Git or SSH.
        self.environment = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LC_ALL": "C.UTF-8",
                            "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
                            "GIT_CONFIG_GLOBAL": "/dev/null"}

    def ensure_key(self):
        if self.key.is_symlink():
            raise SyncError("The managed SSH private key must not be a symlink.")
        if self.key.exists():
            if not self.key.is_file() or self.key.stat().st_mode & 0o077:
                raise SyncError("The managed SSH private key must be a regular file with mode 0600.")
            return False
        result = self.process(["/usr/bin/ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                               f"work-repositories@{socket.gethostname()}", "-f", str(self.key)])
        if result.returncode:
            raise SyncError("Could not create this server's GitHub SSH identity.")
        return True

    def process(self, args, timeout=60):
        return subprocess.run(args, env=self.environment, cwd=self.home, text=True,
                              capture_output=True, timeout=timeout)

    def run(self, *args, timeout=60):
        result = self.process(["/usr/bin/git", *map(str, args)], timeout)
        if result.returncode:
            raise SyncError("Git failed; the checkout was preserved. Check its local state and SSH access.")
        return result.stdout.strip()

    def authenticate(self, owner):
        result = self.process([*self.ssh, "-T", "git@github.com"])
        output = result.stdout + result.stderr
        if result.returncode == 1 and f"hi {owner.lower()}! you've successfully authenticated," in output.lower():
            return True
        if re.search(r"Permission denied \([^)]*\bpublickey\b[^)]*\)", output):
            return False
        raise SyncError("GitHub SSH verification failed; check network access, account, and pinned host key.")

    def enroll(self, api, owner):
        if self.authenticate(owner):
            return False
        created = self.ensure_key()
        # Dedicated identities ignore unrelated user SSH settings. Existing working
        # access above keeps its normal configuration and requires no new key.
        self.ssh = [self.ssh[0], "-F", "/dev/null", "-i", str(self.key), *self.ssh[1:]]
        if self.authenticate(owner):
            return created
        public = self.process(["/usr/bin/ssh-keygen", "-y", "-f", str(self.key)])
        if public.returncode or not public.stdout.startswith("ssh-ed25519 "):
            raise SyncError("Cannot read the managed SSH public key; the private key was not changed.")
        key = " ".join(public.stdout.split()[:2])
        changed = False
        try:
            keys = list(api.pages("/user/keys"))
            if not any(" ".join(item.get("key", "").split()[:2]) == key for item in keys):
                api.request("/user/keys", {"key": key, "title": f"work-repositories: {socket.gethostname()}"})
                changed = True
        except ApiError:
            raise SyncError("GitHub key registration failed. Grant the token Git SSH keys write access, "
                            "or add this server's id_ed25519_github_work.pub to GitHub manually.") from None
        for attempt in range(5):
            if self.authenticate(owner):
                return changed or created
            if attempt < 4:
                time.sleep(2)
        raise SyncError("The managed GitHub SSH key is not authorized yet; retry after confirming its registration.")

    def clone(self, repo, destination):
        self.run("-c", "core.hooksPath=/dev/null", "clone", "--no-recurse-submodules",
                 "--config", "core.sshCommand=" + shlex.join(self.ssh),
                 f"git@github.com:{repo['repo']}.git", destination, timeout=600)

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
