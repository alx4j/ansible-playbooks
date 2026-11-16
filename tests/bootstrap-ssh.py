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


def main():
    source = Path(__file__).resolve().parents[1]
    password = secrets.token_urlsafe(32)
    initial_password = secrets.token_urlsafe(32)
    dev_password = secrets.token_urlsafe(32)
    subprocess.run(["useradd", "-m", "-s", "/bin/bash", "-G", "sudo", "initialadmin"], check=True)
    subprocess.run(
        ["chpasswd"], input=f"root:{password}\ninitialadmin:{initial_password}\n",
        text=True, check=True, capture_output=True,
    )
    sudo_policy = Path("/etc/sudoers.d/initialadmin-test")
    sudo_policy.write_text("Defaults:initialadmin timestamp_timeout=0\nDefaults:dev timestamp_timeout=0\n")
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
        fixture[0]["vars"] = yaml.safe_load((source / "bootstrap.yml").read_text())[0]["vars"]
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
                "ansible_user": "bootstraptest", "ansible_python_interpreter": "/usr/bin/python3",
                "dev_host_user": "bootstraptest", "dev_host_passwordless_sudo": True,
                "dev_host_authorized_keys": [public_key],
            }}}}}}
            env = {
                **os.environ, "RUNNER_TEMP": str(runtime), "DEPLOY_PLAYBOOK": "bootstrap",
                "DEPLOY_HOST": "dev01",
                "DEPLOY_INVENTORY": json.dumps(inventory),
                "DEPLOY_KNOWN_HOSTS": f"[127.0.0.1]:{port} {host_key}",
                "DEPLOY_BOOTSTRAP_PASSWORD": password, "DEPLOY_SSH_PRIVATE_KEY": "",
            }

            def deploy(expect_success=True, github=False, **overrides):
                command = ["python3", str(root / "scripts/github-deploy.py")] if github else [str(root / "scripts/deploy")]
                result = subprocess.run(
                    command, cwd=root, env={**env, **overrides},
                    text=True, capture_output=True, timeout=120,
                )
                output = result.stdout + result.stderr
                assert all(value not in output for value in [password, initial_password, dev_password, private_key]), "Credential appeared in output"
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

            # Exercise the combined workflow from a missing dev account through
            # creation, a fresh key/sudo login, and automatic reuse on later runs.
            event = {"inputs": {
                "server_address": "127.0.0.1",
                "bootstrap_user": "initialadmin", "bootstrap_password": initial_password,
            }}
            event_file = root / "event.json"
            event_file.touch(mode=0o600)
            event_file.write_text(json.dumps(event))
            env.update({
                "GITHUB_EVENT_PATH": str(event_file), "DEPLOY_SSH_PORT": str(port),
                "DEPLOY_SSH_PRIVATE_KEY": private_key, "DEV_PASSWORD": dev_password,
            })
            host.update({"ansible_user": "dev", "dev_host_user": "dev"})
            output = deploy(False, DEPLOY_PLAYBOOK="access", DEPLOY_INVENTORY=json.dumps(inventory))
            assert "Permission denied" in output, output
            try:
                pwd.getpwnam("dev")
            except KeyError:
                pass
            else:
                raise AssertionError("The access probe created the development account")
            output = deploy(False, github=True, DEPLOY_KNOWN_HOSTS=f"[127.0.0.1]:{port} {wrong_host_key}")
            assert "Please add this host's fingerprint to your known_hosts file" in output, output
            try:
                pwd.getpwnam("dev")
            except KeyError:
                pass
            else:
                raise AssertionError("An untrusted host key allowed account creation")
            output = deploy(github=True)
            assert "Establishing development access" in output, output
            assert "Configuring the server through development-user SSH key access" in output, output

            output = deploy(False, github=True, DEV_PASSWORD="deliberately incorrect development password")
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
            print("Root/sudo bootstrap, read-only access probe, automatic key handoff, reruns, and rejected credentials passed")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
