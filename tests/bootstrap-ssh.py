"""Exercise password-to-key access with real SSH inside the disposable container.

Focused fixtures import the production account and access tasks. They do not
exercise the full systemd-dependent bootstrap or routine host configuration.
"""

import json
import os
from pathlib import Path
import pwd
import secrets
import shutil
import socket
import subprocess
import tempfile
import time

import yaml


def check_password_only_login(root, user, password, port, host_key):
    """A working password must not qualify as a key-based administrative login."""
    inventory = root / "password-inventory.yml"
    known_hosts = root / "password-known-hosts"
    known_hosts.write_text(f"[127.0.0.1]:{port} {host_key}\n")
    inventory.write_text(yaml.safe_dump({"development": {"hosts": {"fixture": {
        "ansible_host": "127.0.0.1", "ansible_port": port, "ansible_user": user, "sudo_user": user,
        "ansible_ssh_common_args": (f"-o StrictHostKeyChecking=yes -o UserKnownHostsFile={known_hosts} "
                                    "-o IdentitiesOnly=yes -o IdentityFile=none -o IdentityAgent=none"),
    }}}}))
    password_file = root / "password-only"
    password_file.touch(mode=0o600)
    password_file.write_text(password)
    options = ["-i", str(inventory), "--connection-password-file", str(password_file)]
    env = {**os.environ, "ANSIBLE_SSH_PASSWORD_MECHANISM": "ssh_askpass", "ANSIBLE_BECOME_PASS": password}

    def run(argv):
        result = subprocess.run(argv, cwd=root, env=env, text=True, capture_output=True, timeout=60)
        assert password not in result.stdout + result.stderr, "Fixture password appeared in output"
        return result

    login = run(["ansible", "fixture", *options, "-m", "command", "-a", "id -un"])
    assert login.returncode == 0 and user in login.stdout, "Password fixture could not connect"
    probe = run(["ansible-playbook", *options, "access.yml"])
    assert probe.returncode == 4 and "Permission denied" in probe.stdout + probe.stderr, "Expected key-only rejection"


def main():
    source = Path(__file__).resolve().parents[1]
    password = secrets.token_urlsafe(32)
    initial_password = secrets.token_urlsafe(32)
    sudo_password = secrets.token_urlsafe(32)
    sudo_user = "configuredadmin"
    subprocess.run(["useradd", "-m", "-s", "/bin/bash", "-G", "sudo", "initialadmin"], check=True)
    subprocess.run(
        ["chpasswd"], input=f"root:{password}\ninitialadmin:{initial_password}\n",
        text=True, check=True, capture_output=True,
    )
    sudo_policy = Path("/etc/sudoers.d/initialadmin-test")
    sudo_policy.write_text(f"Defaults:initialadmin timestamp_timeout=0\nDefaults:{sudo_user} timestamp_timeout=0\n")
    sudo_policy.chmod(0o440)
    subprocess.run(["visudo", "-cf", str(sudo_policy)], check=True, capture_output=True)

    with tempfile.TemporaryDirectory(prefix="ansible-bootstrap-ssh-") as temporary:
        root = Path(temporary)
        (root / "scripts").mkdir()
        shutil.copy2(source / "scripts/deploy", root / "scripts/deploy")
        shutil.copy2(source / "scripts/github-deploy.py", root / "scripts/github-deploy.py")
        shutil.copy2(source / "access.yml", root / "access.yml")
        fixture = yaml.safe_load((source / "tests/fixtures/bootstrap-access.yml").read_text())
        # Exercise the actual bootstrap user/escalation expressions while the
        # fixture limits remote work to account tasks supported by containers.
        bootstrap = yaml.safe_load((source / "bootstrap.yml").read_text())[0]
        fixture[0]["vars"] = bootstrap["vars"]
        fixture[0]["become"] = bootstrap["become"]
        (root / "bootstrap.yml").write_text(yaml.safe_dump(fixture, sort_keys=False))
        shutil.copy2(source / "access.yml", root / "site.yml")
        (root / "ansible.cfg").write_text(f"[defaults]\nroles_path = {source / 'roles'}\n")
        runtime = root / "runtime"
        runtime.mkdir()

        # Select an unused loopback port inside this isolated container.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        config = root / "sshd_config"
        config.write_text(
            f"Port {port}\nListenAddress 127.0.0.1\n"
            "HostKey /etc/ssh/ssh_host_ed25519_key\n"
            "PermitRootLogin yes\nPasswordAuthentication yes\n"
            "KbdInteractiveAuthentication no\nUsePAM yes\n"
            "AuthorizedKeysFile .ssh/authorized_keys\nStrictModes yes\n"
            f"PidFile {root / 'sshd.pid'}\n"
            "Subsystem sftp internal-sftp\n"
        )
        subprocess.run(["/usr/sbin/sshd", "-t", "-f", str(config)], check=True)
        server = subprocess.Popen(
            ["/usr/sbin/sshd", "-D", "-e", "-f", str(config)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        try:
            for _ in range(50):
                if server.poll() is not None:
                    raise AssertionError("Test SSH server exited during startup")
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                        break
                except OSError:
                    time.sleep(0.1)
            else:
                raise AssertionError("Test SSH server did not become ready")

            private_key = Path("/tmp/ansible-test-key").read_text()
            public_key = Path("/tmp/ansible-test-key.pub").read_text().strip()
            host_key = Path("/etc/ssh/ssh_host_ed25519_key.pub").read_text().strip()
            inventory = {"all": {"children": {"development": {"hosts": {"dev01": {
                "ansible_host": "127.0.0.1", "ansible_port": port,
                "sudo_user": "bootstraptest", "ansible_python_interpreter": "/usr/bin/python3",
                "dev_host_passwordless_sudo": True,
                "dev_host_authorized_keys": [public_key],
            }}}}}}
            env = {
                **os.environ, "RUNNER_TEMP": str(runtime), "DEPLOY_PLAYBOOK": "bootstrap",
                "DEPLOY_HOST": "dev01",
                "DEPLOY_INVENTORY": json.dumps(inventory),
                "DEPLOY_KNOWN_HOSTS": f"[127.0.0.1]:{port} {host_key}",
                "DEPLOY_BOOTSTRAP_PASSWORD": password, "DEPLOY_SSH_PRIVATE_KEY": "",
            }

            def deploy(expect_success=True, github=False, update=False, **overrides):
                command = ["python3", str(root / "scripts/github-deploy.py")] if github else [str(root / "scripts/deploy")]
                if update:
                    command.append("--update-inventory")
                result = subprocess.run(
                    command, cwd=root, env={**env, **overrides},
                    text=True, capture_output=True, timeout=120,
                )
                output = result.stdout + result.stderr
                assert all(value not in output for value in [password, initial_password, sudo_password, private_key]), "Credential appeared in output"
                assert not list(runtime.iterdir()), "Temporary credentials were not removed"
                if expect_success:
                    assert result.returncode == 0, output
                else:
                    assert result.returncode != 0, "Invalid SSH credentials unexpectedly succeeded"
                return output

            wrong_host_key = Path("/tmp/unrelated-key.pub").read_text().strip()
            output = deploy(False, DEPLOY_KNOWN_HOSTS=f"[127.0.0.1]:{port} {wrong_host_key}")
            assert "Please add this host's fingerprint to your known_hosts file" in output, output
            output = deploy(False, DEPLOY_BOOTSTRAP_PASSWORD="deliberately incorrect test password")
            assert "Permission denied" in output, output
            deploy()
            # Keep the initial password in the caller's environment to ensure
            # the production runner excludes it from routine key-based access.
            deploy(DEPLOY_PLAYBOOK="site", DEPLOY_SSH_PRIVATE_KEY=private_key)

            host = inventory["all"]["children"]["development"]["hosts"]["dev01"]
            # Keep coverage for inventories using the original connection and role variables.
            host.pop("sudo_user")
            host.update({
                "dev_host_bootstrap_user": "initialadmin",
                "ansible_user": "bootstrapviaadmin", "dev_host_user": "bootstrapviaadmin",
            })
            env.update({
                "DEPLOY_INVENTORY": json.dumps(inventory),
                "DEPLOY_BOOTSTRAP_PASSWORD": initial_password,
                "ANSIBLE_BECOME_PASS": "deliberately different routine sudo password",
            })
            output = deploy(False, DEPLOY_BOOTSTRAP_BECOME_PASSWORD="deliberately incorrect sudo password")
            assert "Incorrect sudo password" in output, output
            deploy()
            deploy(DEPLOY_PLAYBOOK="site", DEPLOY_SSH_PRIVATE_KEY=private_key)

            # Exercise the combined workflow from a missing configured account through
            # creation, a fresh key/sudo login, and automatic reuse on later runs.
            registered_inventory = root / "inventories/development/hosts.yml"
            registered_inventory.parent.mkdir(parents=True)
            registered_inventory.write_text(yaml.safe_dump({"all": {"children": {"development": {"hosts": {
                "testserver": {
                    "ansible_host": "127.0.0.1", "ansible_port": port,
                },
            }}}}}))
            event = {"inputs": {"server_address": "testserver", "bootstrap_user": "initialadmin",
                                "bootstrap_password": initial_password}}
            event_file = root / "event.json"
            event_file.touch(mode=0o600)
            event_file.write_text(json.dumps(event))
            env.update({
                "GITHUB_EVENT_PATH": str(event_file), "DEPLOY_SSH_PORT": str(port),
                "DEPLOY_SSH_PRIVATE_KEY": private_key, "SUDO_PASSWORD": sudo_password,
                "DEPLOY_SUDO_USER": sudo_user,
            })
            host.update({"ansible_user": sudo_user, "dev_host_user": sudo_user})
            output = deploy(False, DEPLOY_PLAYBOOK="access", DEPLOY_INVENTORY=json.dumps(inventory))
            assert "Permission denied" in output, output
            try:
                pwd.getpwnam(sudo_user)
            except KeyError:
                pass
            else:
                raise AssertionError("The access probe created the development account")
            output = deploy(False, github=True, DEPLOY_KNOWN_HOSTS=f"[127.0.0.1]:{port} {wrong_host_key}")
            assert "Please add this host's fingerprint to your known_hosts file" in output, output
            try:
                pwd.getpwnam(sudo_user)
            except KeyError:
                pass
            else:
                raise AssertionError("An untrusted host key allowed account creation")
            # Automatic runs must not bootstrap, even when valid initial credentials
            # are available in an event or a stale caller environment.
            output = deploy(github=True, update=True)
            assert "::warning::Skipped testserver" in output, output
            assert "Establishing development access" not in output, output
            try:
                pwd.getpwnam(sudo_user)
            except KeyError:
                pass
            else:
                raise AssertionError("An automatic inventory run created the missing account")
            event["inputs"]["bootstrap_password"] = "deliberately incorrect initial password"
            event_file.write_text(json.dumps(event))
            output = deploy(False, github=True)
            assert "Permission denied" in output, output
            event["inputs"]["bootstrap_password"] = initial_password
            event_file.write_text(json.dumps(event))
            output = deploy(github=True)
            assert "Establishing development access" in output, output
            assert "Configuring the server through development-user SSH key access" in output, output

            check_password_only_login(root, sudo_user, sudo_password, port, host_key)

            output = deploy(False, github=True, SUDO_PASSWORD="deliberately incorrect development password")
            assert "Incorrect sudo password" in output, output
            assert "Establishing development access" not in output, "Sudo failure triggered bootstrap"
            event["inputs"].update(bootstrap_user="unusedadmin", bootstrap_password="obsolete initial password")
            event_file.write_text(json.dumps(event))
            output = deploy(github=True)
            assert "Establishing development access" not in output, "Rerun used the initial administrator"
            assert "changed=0" in output, "Repeated configuration did not converge"
            event["inputs"] = {"server_address": "127.0.0.1"}
            event_file.write_text(json.dumps(event))
            deploy(github=True)

            output = deploy(github=True, update=True, SUDO_PASSWORD="deliberately incorrect development password")
            assert "Incorrect sudo password" in output and "::warning::Skipped testserver" in output, output
            output = deploy(github=True, update=True, DEPLOY_KNOWN_HOSTS=f"[127.0.0.1]:{port} {wrong_host_key}")
            assert "::warning::Skipped testserver" in output, output

            # One inaccessible server must not prevent an existing server from updating.
            registered = yaml.safe_load(registered_inventory.read_text())
            registered["all"]["children"]["development"]["hosts"]["unready"] = {
                "ansible_host": "127.0.0.2", "ansible_port": port,
            }
            registered_inventory.write_text(yaml.safe_dump(registered))
            summary = root / "summary.md"
            output = deploy(github=True, update=True, GITHUB_STEP_SUMMARY=str(summary))
            assert "| testserver | Updated |" in summary.read_text(), output
            assert "| unready | Skipped:" in summary.read_text(), output
            assert "Establishing development access" not in output, output
            assert "changed=0" in output, output
            print("Root/sudo bootstrap, key handoff, reruns, automatic inventory updates, and unready-server skips passed")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
