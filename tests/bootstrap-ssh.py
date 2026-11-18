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
    second_password = secrets.token_urlsafe(32)
    sudo_user = "configuredadmin"
    for admin in ("initialadmin", "secondadmin"):
        subprocess.run(["useradd", "-m", "-s", "/bin/bash", "-G", "sudo", admin], check=True)
    subprocess.run(
        ["chpasswd"], input=f"root:{password}\ninitialadmin:{initial_password}\nsecondadmin:{second_password}\n",
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
        shutil.copy2(source / "upload-public-key.yml", root / "upload-public-key.yml")
        fixture = yaml.safe_load((source / "tests/fixtures/bootstrap-access.yml").read_text())
        # Exercise the actual bootstrap user/escalation expressions while the
        # fixture limits remote work to account tasks supported by containers.
        bootstrap = yaml.safe_load((source / "bootstrap.yml").read_text())[0]
        fixture[0]["vars"] = bootstrap["vars"]
        fixture[0]["become"] = bootstrap["become"]
        (root / "bootstrap.yml").write_text(yaml.safe_dump(fixture, sort_keys=False))
        site = yaml.safe_load((source / "access.yml").read_text())
        site[0]["tasks"].append({
            "name": "Retire the bootstrap password after verified replacement access",
            "ansible.builtin.import_role": {"name": "dev_host", "tasks_from": "finish_bootstrap_password"},
            "when": "dev_host_finish_bootstrap_password | default(false) | bool",
        })
        site[0]["tasks"].append({
            "name": "Maintain the development account and its authorized keys",
            "ansible.builtin.import_role": {"name": "dev_host", "tasks_from": "account"},
        })
        (root / "site.yml").write_text(yaml.safe_dump(site, sort_keys=False))
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

            def deploy(expect_success=True, github=False, update=False, upload=False, **overrides):
                command = ["python3", str(root / "scripts/github-deploy.py")] if github else [str(root / "scripts/deploy")]
                if update:
                    command.append("--update-inventory")
                if upload:
                    command.append("--upload-public-key")
                result = subprocess.run(
                    command, cwd=root, env={**env, **overrides},
                    text=True, capture_output=True, timeout=120,
                )
                output = result.stdout + result.stderr
                assert all(value not in output for value in [password, initial_password, second_password, sudo_password, private_key]), "Credential appeared in output"
                assert not list(runtime.iterdir()), "Temporary credentials were not removed"
                if expect_success:
                    assert result.returncode == 0, output
                else:
                    assert result.returncode != 0, "Invalid SSH credentials unexpectedly succeeded"
                return output

            def password_status(user):
                status = subprocess.check_output(["passwd", "--status", "--", user], text=True)
                return status.split()[1]

            wrong_host_key = Path("/tmp/unrelated-key.pub").read_text().strip()
            output = deploy(False, DEPLOY_KNOWN_HOSTS=f"[127.0.0.1]:{port} {wrong_host_key}")
            assert "Please add this host's fingerprint to your known_hosts file" in output, output
            output = deploy(False, DEPLOY_BOOTSTRAP_PASSWORD="deliberately incorrect test password")
            assert "Permission denied" in output, output
            deploy()
            assert password_status("root") == "P"
            deploy(False, DEPLOY_PLAYBOOK="site", DEPLOY_SSH_PRIVATE_KEY=Path("/tmp/unrelated-key").read_text())
            assert password_status("root") == "P", "A failed key login retired the initial password"
            # Keep the initial password in the caller's environment to ensure
            # the production runner excludes it from routine key-based access.
            deploy(DEPLOY_PLAYBOOK="site", DEPLOY_SSH_PRIVATE_KEY=private_key)

            assert password_status("root") == "L", "Root's console password survived the verified handoff"
            assert password_status("initialadmin") == "P"
            host = inventory["all"]["children"]["development"]["hosts"]["dev01"]
            # Keep coverage for inventories using the original connection and role variables.
            host.pop("sudo_user")
            host.update({
                "dev_host_bootstrap_user": "initialadmin", "dev_host_passwordless_sudo": False,
                "ansible_user": "bootstrapviaadmin", "dev_host_user": "bootstrapviaadmin",
            })
            dev_hash = subprocess.check_output(
                ["openssl", "passwd", "-6", "-stdin"], input=sudo_password + "\n", text=True,
            ).strip()
            env.update({
                "SUDO_PASSWORD_HASH": dev_hash,
                "DEPLOY_INVENTORY": json.dumps(inventory),
                "DEPLOY_BOOTSTRAP_PASSWORD": initial_password,
                "ANSIBLE_BECOME_PASS": sudo_password,
            })
            output = deploy(False, DEPLOY_BOOTSTRAP_BECOME_PASSWORD="deliberately incorrect sudo password")
            assert "Incorrect sudo password" in output, output
            deploy()
            output = deploy(False, DEPLOY_PLAYBOOK="site", DEPLOY_SSH_PRIVATE_KEY=private_key,
                            ANSIBLE_BECOME_PASS="deliberately incorrect development sudo password")
            assert "Incorrect sudo password" in output, output
            assert password_status("initialadmin") == "P", "A failed sudo check retired the initial password"
            deploy(DEPLOY_PLAYBOOK="site", DEPLOY_SSH_PRIVATE_KEY=private_key)

            assert password_status("initialadmin") == "L"
            assert password_status("secondadmin") == "P"

            # Exercise the combined workflow from a missing configured account through
            # creation, a fresh key/sudo login, and automatic reuse on later runs.
            registered_inventory = root / "inventories/development/hosts.yml"
            registered_inventory.parent.mkdir(parents=True)
            registered_inventory.write_text(yaml.safe_dump({"all": {"children": {"development": {"hosts": {
                f"{sudo_user}@testserver": {
                    "ansible_host": "127.0.0.1", "ansible_port": port,
                },
            }}}}}))
            event = {"inputs": {"server_address": "testserver", "bootstrap_user": "secondadmin",
                                "bootstrap_password": second_password, "sudo_user": sudo_user,
                                "user_public_key": wrong_host_key}}
            event_file = root / "event.json"
            event_file.touch(mode=0o600)
            event_file.write_text(json.dumps(event))
            env.update({
                "GITHUB_EVENT_PATH": str(event_file), "DEPLOY_SSH_PORT": str(port),
                "DEPLOY_SSH_PRIVATE_KEY": private_key, "SUDO_PASSWORD": sudo_password,
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
            assert f"::warning::Skipped {sudo_user}@testserver" in output, output
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
            event["inputs"]["bootstrap_password"] = second_password
            event_file.write_text(json.dumps(event))
            output = deploy(github=True)
            assert "Establishing development access" in output, output
            assert "Configuring the server through development-user SSH key access" in output, output
            authorized_keys = Path(pwd.getpwnam(sudo_user).pw_dir) / ".ssh/authorized_keys"
            assert wrong_host_key.split()[1] in authorized_keys.read_text()

            check_password_only_login(root, sudo_user, sudo_password, port, host_key)

            output = deploy(False, github=True, SUDO_PASSWORD="deliberately incorrect development password")
            assert "Incorrect sudo password" in output, output
            assert "Establishing development access" not in output, "Sudo failure triggered bootstrap"
            assert password_status("secondadmin") == "L"
            assert password_status(sudo_user) == "P"
            event["inputs"].update(bootstrap_user="unusedadmin", bootstrap_password="obsolete initial password")
            event_file.write_text(json.dumps(event))
            output = deploy(github=True)
            assert "Establishing development access" not in output, "Rerun used the initial administrator"
            assert "changed=0" in output, "Repeated configuration did not converge"
            event["inputs"] = {"server_address": "127.0.0.1", "sudo_user": sudo_user, "user_public_key": ""}
            event_file.write_text(json.dumps(event))
            keys_before = authorized_keys.read_bytes()
            deploy(github=True)
            assert authorized_keys.read_bytes() == keys_before

            output = deploy(github=True, update=True, SUDO_PASSWORD="deliberately incorrect development password")
            assert "Incorrect sudo password" in output and f"::warning::Skipped {sudo_user}@testserver" in output, output
            output = deploy(github=True, update=True, DEPLOY_KNOWN_HOSTS=f"[127.0.0.1]:{port} {wrong_host_key}")
            assert f"::warning::Skipped {sudo_user}@testserver" in output, output

            # One inaccessible server must not prevent an existing server from updating.
            registered = yaml.safe_load(registered_inventory.read_text())
            registered["all"]["children"]["development"]["hosts"]["dev@unready"] = {
                "ansible_host": "127.0.0.2", "ansible_port": port,
            }
            registered_inventory.write_text(yaml.safe_dump(registered))
            summary = root / "summary.md"
            output = deploy(github=True, update=True, GITHUB_STEP_SUMMARY=str(summary))
            assert f"| {sudo_user}@testserver | Updated |" in summary.read_text(), output
            assert "| dev@unready | Skipped:" in summary.read_text(), output
            assert "Establishing development access" not in output, output
            assert "changed=0" in output, output
            known_hosts = root / "user_known_hosts"
            known_hosts.write_text(f"[127.0.0.1]:{port} {host_key}\n")
            login = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=yes",
                 "-o", f"UserKnownHostsFile={known_hosts}", "-i", "/tmp/unrelated-key", "-p", str(port),
                 f"{sudo_user}@127.0.0.1", "id -un"], text=True, capture_output=True, check=True,
            )
            assert login.stdout.strip() == sudo_user

            subprocess.run(["gpasswd", "-d", sudo_user, "sudo"], check=True, capture_output=True)
            authorized_keys.chmod(0o644)
            upload_identity = root / "workstation"
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(upload_identity)], check=True)
            uploaded_key = upload_identity.with_suffix(".pub").read_text()
            event["inputs"] = {"server_address": "testserver", "username": sudo_user, "user_public_key": uploaded_key}
            event_file.write_text(json.dumps(event))
            before = authorized_keys.read_bytes()
            deploy(False, github=True, upload=True, DEPLOY_KNOWN_HOSTS=f"[127.0.0.1]:{port} {wrong_host_key}")
            assert authorized_keys.read_bytes() == before
            upload_result = deploy(github=True, upload=True, SUDO_PASSWORD="")
            assert "changed=1" in upload_result
            after = authorized_keys.read_bytes()
            assert authorized_keys.stat().st_mode & 0o777 == 0o644
            assert after.startswith(before) and after.count(uploaded_key.split()[1].encode()) == 1
            upload_login = login.args.copy()
            upload_login[upload_login.index("-i") + 1] = str(upload_identity)
            assert subprocess.check_output(upload_login, text=True).strip() == sudo_user
            assert "changed=0" in deploy(github=True, upload=True, SUDO_PASSWORD="")
            assert authorized_keys.read_bytes() == after
            # A duplicate upload must also preserve options on an existing key.
            restricted = before + b"restrict " + uploaded_key.encode()
            authorized_keys.write_bytes(restricted)
            assert "changed=0" in deploy(github=True, upload=True, SUDO_PASSWORD="")
            assert authorized_keys.read_bytes() == restricted
            print("Standalone SSH key upload, fingerprint verification, repeat uploads, and restricted keys passed")
            print("Root/sudo bootstrap, runner and personal SSH access, reruns, per-user updates, and skips passed")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait()


if __name__ == "__main__":
    main()
