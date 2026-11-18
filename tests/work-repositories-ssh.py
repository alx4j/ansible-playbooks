"""Verify managed SSH cloning, pushes, and role setup in a disposable container."""

import os
from pathlib import Path
import pwd
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time

from test_work_repositories import FakeGitHub, SYNC, legacy_archive, repository


SOURCE = Path(__file__).resolve().parents[1]


def git_server():
    assert pwd.getpwuid(os.getuid()).pw_name == "git"
    command = shlex.split(os.environ.get("SSH_ORIGINAL_COMMAND", ""))
    if not command:
        print("Hi fixture! You've successfully authenticated, but GitHub does not provide shell access.")
        sys.exit(1)
    assert len(command) == 2 and command[0] in ("git-upload-pack", "git-receive-pack")
    assert command[1] == "fixture/one.git"
    os.execv("/usr/bin/" + command[0], [command[0], "/home/git/fixture/one.git"])


def client(port):
    assert pwd.getpwuid(os.getuid()).pw_name == "worktest"
    home = Path.home()
    projects = home / "test-projects"
    options = ["-o", "HostName=127.0.0.1", "-p", str(port), "-o", "HostKeyAlias=fixture",
               "-o", f"UserKnownHostsFile={home}/.ssh/fixture_known_hosts"]
    git = SYNC.Git(home, options)
    api = FakeGitHub([repository()])
    def sync():
        return SYNC.Reconciler(home, projects, "fixture", "work", api, git).run()
    try:
        assert not git.enroll(api, "fixture")
    except SYNC.SyncError:
        probe = git.process([*git.ssh, "-T", "git@github.com"])
        raise AssertionError(probe.stdout + probe.stderr) from None
    first = sync()
    assert first["cloned"] == ["fixture/one"], first
    path = projects / "one"
    assert git.run("-C", path, "remote", "get-url", "origin") == "git@github.com:fixture/one.git"
    assert not sync()["changed"]
    (path / "pushed").write_text("written on the development VPS\n")
    git.run("-C", path, "add", ".")
    git.run("-C", path, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "push fixture")
    git.run("-C", path, "push", "origin", "main")
    api.repositories[0]["topics"] = []
    assert not sync()["changed"]
    assert path.is_dir() and not (projects / ".archive").exists()
    api.repositories[0]["topics"] = ["work"]
    kept = sync()
    assert not kept["changed"] and kept["kept"] == ["fixture/one"], kept

    archive = legacy_archive(projects, home / ".local/state/work-repositories/test-projects.json")
    active = subprocess.Popen(["sleep", "60"], cwd=archive)
    try:
        busy = sync()
        assert not busy["changed"] and busy["skipped"], busy
    finally:
        active.terminate()
        active.wait()
    assert sync()["restored"] == ["fixture/one"]
    assert not sync()["changed"]
    assert (path / "pushed").read_text() == "written on the development VPS\n"
    # An existing account SSH configuration must work without enrolling another key.
    (home / ".ssh/config").write_text(f"Host github.com\n  IdentityFile {git.key}\n")
    existing = SYNC.Git(home, options)
    assert not existing.enroll(api, "fixture")
    assert "-i" not in existing.ssh
    # A mismatched host key must fail before any GitHub key registration or clone.
    trust = home / ".ssh/fixture_known_hosts"
    trust.write_text("fixture " + (home / ".ssh/id_ed25519_github_work.pub").read_text())
    try:
        git.authenticate("fixture")
    except SYNC.SyncError:
        pass
    else:
        raise AssertionError("SSH accepted a mismatched server host key")
    print("SSH clone/push, host-key rejection, topic removal, legacy restore safeguards, and zero-change reruns passed")


def main():
    import yaml

    assert os.getuid() == 0 and (Path("/run/.containerenv").exists() or Path("/.dockerenv").exists())
    for user in ("worktest", "git"):
        subprocess.run(["useradd", "-m", "-s", "/bin/bash", "-p", "*", user], check=True)
    with tempfile.TemporaryDirectory(prefix="work-ssh-") as temporary:
        root = Path(temporary)
        play = [{"name": "Install production repository prerequisites in the fixture",
                 "hosts": "localhost", "connection": "local", "gather_facts": False,
                 "vars": {"dev_host_user": "worktest", "dev_host_projects_dir": "test-projects"},
                 "tasks": [
                     {"name": "Create the production projects directory", "ansible.builtin.import_role":
                      {"name": "dev_host", "tasks_from": "projects"}},
                     {"name": "Install shared GitHub access", "ansible.builtin.import_role":
                      {"name": "github_access", "tasks_from": "setup"}},
                     {"name": "Apply the production repository setup", "ansible.builtin.import_role":
                      {"name": "work_repositories", "tasks_from": "setup"}}]}]
        playbook = root / "setup.yml"
        playbook.write_text(yaml.safe_dump(play, sort_keys=False))
        environment = {**os.environ, "ANSIBLE_ROLES_PATH": str(SOURCE / "roles")}
        # A private directory owned by another account needs an explicit repair.
        ssh_directory = Path("/home/worktest/.ssh")
        ssh_directory.mkdir(mode=0o700)
        refused = subprocess.run(["ansible-playbook", "-i", "localhost,", str(playbook)],
                                 env=environment, capture_output=True, text=True)
        assert refused.returncode != 0 and "private SSH directory" in refused.stdout
        assert ssh_directory.stat().st_uid == 0
        ssh_directory.rmdir()
        for iteration in range(2):
            result = subprocess.run(["ansible-playbook", "-i", "localhost,", str(playbook)],
                                    env=environment, text=True, capture_output=True, timeout=120)
            assert result.returncode == 0, result.stdout + result.stderr
            if iteration:
                assert re.search(r"localhost\s+:.*changed=0\s+unreachable=0\s+failed=0", result.stdout), result.stdout
        home = Path("/home/worktest")
        legacy_trust = Path("/usr/local/lib/work-repositories/github_known_hosts")
        assert legacy_trust.is_symlink()
        assert legacy_trust.resolve() == Path("/usr/local/lib/github-access/github_known_hosts")
        subprocess.run(["runuser", "-u", "worktest", "--", "python3", str(Path(__file__).resolve()), "--key"], check=True)
        key = home / ".ssh/id_ed25519_github_work"
        assert key.stat().st_mode & 0o777 == 0o600
        assert key.stat().st_uid == pwd.getpwnam("worktest").pw_uid
        ssh_directory = Path("/home/git/.ssh")
        ssh_directory.mkdir(mode=0o700)
        (ssh_directory / "authorized_keys").write_text(key.with_suffix(".pub").read_text())
        (ssh_directory / "authorized_keys").chmod(0o600)
        upstream = Path("/home/git/fixture/one.git")
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(upstream)], check=True)
        seed = root / "seed"
        subprocess.run(["git", "init", "-q", "-b", "main", str(seed)], check=True)
        (seed / "initial").write_text("fixture\n")
        subprocess.run(["git", "-C", str(seed), "add", "."], check=True)
        subprocess.run(["git", "-C", str(seed), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                        "commit", "-qm", "initial"], check=True)
        subprocess.run(["git", "-C", str(seed), "push", "-q", str(upstream), "main"], check=True)
        subprocess.run(["chown", "-R", "git:git", "/home/git"], check=True)
        trust = home / ".ssh/fixture_known_hosts"
        trust.write_text("fixture " + Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text())
        subprocess.run(["chown", "worktest:worktest", str(trust)], check=True)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = root / "sshd_config"
        config.write_text(f"Port {port}\nListenAddress 127.0.0.1\n"
                          "HostKey /etc/ssh/ssh_host_ed25519_key\nPasswordAuthentication no\nUsePAM yes\n"
                          "AuthorizedKeysFile .ssh/authorized_keys\nAllowUsers git\nStrictModes yes\n"
                          f"ForceCommand /usr/bin/python3 {Path(__file__).resolve()} --server\n")
        server = subprocess.Popen(["/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            for _ in range(50):
                assert server.poll() is None, "Fixture SSH server exited"
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            subprocess.run(["runuser", "-u", "worktest", "--", "python3", str(Path(__file__).resolve()),
                            "--client", str(port)], check=True, timeout=180)
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    if sys.argv[1:] == ["--server"]:
        git_server()
    elif sys.argv[1:] == ["--key"]:
        assert pwd.getpwuid(os.getuid()).pw_name == "worktest"
        identity = SYNC.Git(Path.home())
        assert identity.ensure_key()
        assert not identity.ensure_key()
    elif len(sys.argv) == 3 and sys.argv[1] == "--client":
        client(int(sys.argv[2]))
    else:
        main()
