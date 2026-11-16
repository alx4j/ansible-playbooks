"""Exercise password-to-key access with real SSH inside the disposable container.

Focused fixtures import the production account and access tasks. They do not
exercise the full systemd-dependent bootstrap or routine host configuration.
"""

import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import tempfile
import time


def main():
    source = Path(__file__).resolve().parents[1]
    password = secrets.token_urlsafe(32)
    subprocess.run(["chpasswd"], input=f"root:{password}\n", text=True, check=True, capture_output=True)

    with tempfile.TemporaryDirectory(prefix="ansible-bootstrap-ssh-") as temporary:
        root = Path(temporary)
        (root / "scripts").mkdir()
        shutil.copy2(source / "scripts/deploy", root / "scripts/deploy")
        shutil.copy2(source / "tests/fixtures/bootstrap-access.yml", root / "bootstrap.yml")
        shutil.copy2(source / "tests/fixtures/verify-access.yml", root / "site.yml")
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
                "DEPLOY_MODE": "apply", "DEPLOY_HOST": "dev01",
                "DEPLOY_INVENTORY": json.dumps(inventory),
                "DEPLOY_KNOWN_HOSTS": f"[127.0.0.1]:{port} {host_key}",
                "DEPLOY_BOOTSTRAP_PASSWORD": password, "DEPLOY_SSH_PRIVATE_KEY": "",
            }

            def deploy(expect_success=True, **overrides):
                result = subprocess.run(
                    [str(root / "scripts/deploy")], cwd=root, env={**env, **overrides},
                    text=True, capture_output=True, timeout=120,
                )
                output = result.stdout + result.stderr
                assert password not in output and private_key not in output, "Credential appeared in output"
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
            print("Password bootstrap, host-key rejection, development key login, and sudo checks passed")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
