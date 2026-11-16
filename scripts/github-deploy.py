"""Translate manual workflow inputs into the deployment runner's environment."""

import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def public_key(private_key):
    with tempfile.NamedTemporaryFile(mode="w", prefix="ansible-public-key-") as key_file:
        key_file.write(private_key.rstrip("\n") + "\n")
        key_file.flush()
        result = subprocess.run(
            ["ssh-keygen", "-y", "-P", "", "-f", key_file.name],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
        )
    if result.returncode or not result.stdout.strip():
        raise ValueError("The deployment SSH key is invalid or requires a passphrase.")
    return result.stdout.strip()


def password_hash(password):
    if any(character in password for character in "\r\n\0"):
        raise ValueError("DEV_PASSWORD must be a single line without null characters.")
    # OpenSSL's passwd command otherwise truncates its input to 256 bytes.
    if len(password.encode("utf-8")) > 256:
        raise ValueError("DEV_PASSWORD must be at most 256 UTF-8 bytes.")
    result = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"], input=password + "\n",
        encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
    )
    if result.returncode or not result.stdout.startswith("$6$"):
        raise ValueError("Could not derive the development account password hash.")
    return result.stdout.strip()


def prepare_environment(event, environment):
    if not isinstance(event, dict) or not isinstance(event.get("inputs"), dict):
        raise ValueError("The workflow event must contain deployment inputs.")
    inputs = event.get("inputs", {})
    playbook = inputs.get("playbook", "")
    mode = inputs.get("mode", "")
    if playbook not in ("bootstrap", "site", "maintenance") or mode not in ("check", "apply"):
        raise ValueError("Select a supported playbook and check/apply mode.")

    address = inputs.get("server_address", "")
    if not isinstance(address, str) or not address:
        raise ValueError("Provide the server IP address or hostname.")
    try:
        ipaddress.ip_address(address)
    except ValueError:
        labels = address.removesuffix(".").split(".")
        valid_labels = all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in labels
        )
        if len(address) > 253 or not valid_labels:
            raise ValueError("Provide one IP address or hostname, without a port or SSH options.")

    bootstrap_user = inputs.get("bootstrap_user") or "root"
    if not isinstance(bootstrap_user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", bootstrap_user):
        raise ValueError("Provide a valid initial administrator username.")
    try:
        port = int(environment.get("DEPLOY_SSH_PORT") or "22")
    except ValueError:
        raise ValueError("ANSIBLE_SSH_PORT must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("ANSIBLE_SSH_PORT must be an integer from 1 to 65535.")

    env = environment.copy()
    dev_password = env.pop("DEV_PASSWORD", "")
    if not dev_password:
        raise ValueError("Set the DEV_PASSWORD environment secret for development-user sudo.")
    if not env.get("DEPLOY_SSH_PRIVATE_KEY"):
        raise ValueError("Set ANSIBLE_SSH_PRIVATE_KEY for the development user's deployment key.")
    if not env.get("DEPLOY_KNOWN_HOSTS"):
        raise ValueError("Set verified ANSIBLE_KNOWN_HOSTS entries for the server.")

    # Do not forward unused credentials or the event-file path to Ansible.
    for name in ("DOPPLER_TOKEN", "GITHUB_EVENT_PATH", "DEPLOY_BOOTSTRAP_PASSWORD",
                 "DEPLOY_BOOTSTRAP_BECOME_PASSWORD", "DEV_PASSWORD_HASH", "ANSIBLE_BECOME_PASS"):
        env.pop(name, None)
    if playbook == "bootstrap":
        initial_password = inputs.get("bootstrap_password", "")
        if not isinstance(initial_password, str):
            raise ValueError("Bootstrap password must be text.")
        if initial_password != initial_password.strip() or "\0" in initial_password:
            raise ValueError("Bootstrap password cannot have surrounding whitespace or null characters.")
        env["DEPLOY_BOOTSTRAP_PASSWORD"] = initial_password
        env["DEV_PASSWORD_HASH"] = password_hash(dev_password)
    else:
        env["ANSIBLE_BECOME_PASS"] = dev_password

    host = {
        "ansible_host": address,
        "ansible_port": port,
        "ansible_user": "dev",
        "dev_host_bootstrap_user": bootstrap_user,
        "dev_host_ssh_port": port,
        "dev_host_authorized_keys": [public_key(env["DEPLOY_SSH_PRIVATE_KEY"])],
    }
    inventory = {"all": {"children": {"development": {"hosts": {"dev01": host}}}}}
    env.update({"DEPLOY_HOST": "dev01", "DEPLOY_PLAYBOOK": playbook,
                "DEPLOY_MODE": mode, "DEPLOY_INVENTORY": json.dumps(inventory)})
    return env


def main():
    try:
        with open(os.environ["GITHUB_EVENT_PATH"]) as event_file:
            event = json.load(event_file)
        env = prepare_environment(event, dict(os.environ))
        if os.environ.get("GITHUB_ACTIONS") == "true" and env.get("DEV_PASSWORD_HASH"):
            # This is a runner masking command, not a visible log entry.
            print("::add-mask::" + env["DEV_PASSWORD_HASH"], flush=True)
        result = subprocess.run([str(Path(__file__).with_name("deploy"))], env=env, check=False)
        return result.returncode
    except (OSError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        print("Could not prepare deployment inputs or run the deployment tools.", file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
