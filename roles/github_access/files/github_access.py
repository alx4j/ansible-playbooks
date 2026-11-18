#!/usr/bin/env python3
"""Share verified GitHub API and SSH access between managed integrations."""

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


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


def require_token_owner(api, owner):
    identity = api.request("/user")
    login = identity.get("login") if isinstance(identity, dict) else None
    if not isinstance(login, str) or login.lower() != owner.lower():
        raise SyncError("The Doppler GitHub token must belong to the configured repository owner.")


class Git:
    def __init__(self, home, ssh_options=None):
        self.home = home
        self.key = home / ".ssh/id_ed25519_github_work"
        self.ssh = ["/usr/bin/ssh",
                    "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                    "-o", "ConnectTimeout=15", "-o", "HostKeyAlgorithms=ssh-ed25519",
                    "-o", "GlobalKnownHostsFile=/dev/null"]
        self.ssh += ssh_options or ["-o", "UserKnownHostsFile=/usr/local/lib/github-access/github_known_hosts"]
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
        require_token_owner(api, owner)
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



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner", required=True)
    args = parser.parse_args()
    token = os.environ.pop("GITHUB_TOKEN", "")
    if not token or os.getuid() == 0:
        raise SyncError("Use the Doppler wrapper as the development account.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}", args.owner):
        raise SyncError("Invalid GitHub owner.")
    git = Git(Path.home())
    changed = git.enroll(GitHub(token), args.owner)
    return {"changed": changed, "ssh_options": git.ssh[1:]}


if __name__ == "__main__":
    try:
        print(json.dumps(main(), sort_keys=True))
    except SyncError as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        sys.exit(1)
    except Exception:
        print('{"error":"GitHub access verification failed. No raw diagnostics were emitted."}', file=sys.stderr)
        sys.exit(1)
