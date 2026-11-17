"""Bootstrap one server manually or update inventory servers with existing access."""

import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


INVENTORY = Path(__file__).resolve().parents[1] / "inventories/development/hosts.yml"


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
        raise ValueError("SUDO_PASSWORD must be a single line without null characters.")
    # OpenSSL's passwd command otherwise truncates its input to 256 bytes.
    if len(password.encode("utf-8")) > 256:
        raise ValueError("SUDO_PASSWORD must be at most 256 UTF-8 bytes.")
    result = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"], input=password + "\n",
        encoding="utf-8", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10,
    )
    if result.returncode or not result.stdout.startswith("$6$"):
        raise ValueError("Could not derive the development account password hash.")
    return result.stdout.strip()


def workflow_inputs(event):
    if not isinstance(event, dict) or not isinstance(event.get("inputs"), dict):
        raise ValueError("The workflow event must contain deployment inputs.")
    return event["inputs"]


def server_address(inputs):
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
    return address


def validate_bootstrap_password(password):
    if not isinstance(password, str):
        raise ValueError("Bootstrap password must be text.")
    if password != password.strip() or "\0" in password:
        raise ValueError("Bootstrap password cannot have surrounding whitespace or null characters.")


def load_inventory():
    if not INVENTORY.is_file():
        raise ValueError("The development inventory is missing.")
    result = subprocess.run(
        ["ansible-inventory", "-i", str(INVENTORY), "--list"],
        text=True, capture_output=True, timeout=30, check=False,
        env={name: os.environ[name] for name in (
            "PATH", "HOME", "LANG", "ANSIBLE_CONFIG", "ANSIBLE_COLLECTIONS_PATH", "ANSIBLE_LOCAL_TEMP",
        ) if name in os.environ},
    )
    if result.returncode:
        raise ValueError("Could not resolve the development inventory.")
    inventory = json.loads(result.stdout)
    if not isinstance(inventory, dict):
        raise ValueError("The inventory must define the development group.")
    if "development" not in inventory:
        # Ansible omits empty groups but keeps their names in parent children.
        declared = any("development" in group.get("children", [])
                       for name, group in inventory.items() if name != "_meta" and isinstance(group, dict))
        if not declared:
            raise ValueError("The inventory must define the development group.")
        inventory["development"] = {}
    return inventory


def resolve_target(event):
    inputs = workflow_inputs(event)
    address = server_address(inputs)
    password = inputs.get("bootstrap_password", "")
    validate_bootstrap_password(password)
    inventory = load_inventory()
    hostvars = inventory.get("_meta", {}).get("hostvars", {})
    matches = [
        (name, hostvars.get(name, {})) for name in development_hosts(inventory)
        if address == name or address == hostvars.get(name, {}).get("ansible_host")
    ]
    if len(matches) > 1:
        raise ValueError("The server address matches multiple inventory entries; use a unique hostname.")
    name, saved = matches[0] if matches else ("dev01", {})
    address = server_address({"server_address": saved.get("ansible_host", address)})
    return name, {**saved, "ansible_host": address}


def development_hosts(inventory):
    hosts = set()
    visited = set()
    pending = ["development"]
    while pending:
        group = pending.pop()
        if group not in visited:
            visited.add(group)
            hosts.update(inventory.get(group, {}).get("hosts", []))
            pending.extend(inventory.get(group, {}).get("children", []))
    return sorted(hosts)


def prepare_environment(event, environment):
    inputs = workflow_inputs(event)
    address = server_address(inputs)

    bootstrap_user = inputs.get("bootstrap_user") or "root"
    if not isinstance(bootstrap_user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", bootstrap_user):
        raise ValueError("Provide a valid initial administrator username.")
    validate_bootstrap_password(inputs.get("bootstrap_password", ""))
    try:
        port = int(environment.get("DEPLOY_SSH_PORT") or "22")
    except ValueError:
        raise ValueError("ANSIBLE_SSH_PORT must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("ANSIBLE_SSH_PORT must be an integer from 1 to 65535.")

    env = environment.copy()
    sudo_user = env.pop("DEPLOY_SUDO_USER", "")
    if not isinstance(sudo_user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", sudo_user) or sudo_user == "root":
        raise ValueError("Set SUDO_USER to a valid non-root account name in the development environment.")
    sudo_password = env.pop("SUDO_PASSWORD", "")
    if not sudo_password:
        raise ValueError("Set the SUDO_PASSWORD environment secret for development-user sudo.")
    if not env.get("DEPLOY_SSH_PRIVATE_KEY"):
        raise ValueError("Set ANSIBLE_SSH_PRIVATE_KEY for the development user's deployment key.")
    if not env.get("DEPLOY_KNOWN_HOSTS"):
        raise ValueError("Set verified ANSIBLE_KNOWN_HOSTS entries for the server.")

    # Do not forward unused credentials or the event-file path to Ansible.
    for name in ("DOPPLER_TOKEN", "DEPLOY_DOPPLER_TOKEN", "GITHUB_EVENT_PATH", "DEPLOY_BOOTSTRAP_PASSWORD",
                 "DEPLOY_BOOTSTRAP_SECRET", "DEPLOY_BOOTSTRAP_BECOME_PASSWORD",
                 "SUDO_PASSWORD_HASH", "DEPLOY_PLAYBOOK"):
        env.pop(name, None)
    env["ANSIBLE_BECOME_PASS"] = sudo_password
    if environment.get("DOPPLER_TOKEN"):
        env["DEPLOY_DOPPLER_TOKEN"] = environment["DOPPLER_TOKEN"]

    host = {
        "ansible_host": address,
        "ansible_port": port,
        "sudo_user": sudo_user,
        "dev_host_bootstrap_user": bootstrap_user,
        "dev_host_ssh_port": port,
        "dev_host_authorized_keys": [public_key(env["DEPLOY_SSH_PRIVATE_KEY"])],
    }
    inventory = {"all": {"children": {"development": {"hosts": {"dev01": host}}}}}
    env.update({"DEPLOY_HOST": "dev01", "DEPLOY_INVENTORY": json.dumps(inventory)})
    return env


def run_playbook(env, playbook, capture_output=False):
    child_env = {**env, "DEPLOY_PLAYBOOK": playbook}
    token = child_env.pop("DEPLOY_DOPPLER_TOKEN", "")
    child_env.pop("DOPPLER_TOKEN", None)
    if playbook == "site" and token:
        child_env["DOPPLER_TOKEN"] = token
    return subprocess.run(
        [str(Path(__file__).with_name("deploy"))],
        env=child_env,
        text=True, capture_output=capture_output, check=False,
    )


def configure_server(env, initial_password):
    print("Checking development-user SSH and sudo access.", flush=True)
    access = run_playbook(env, "access", capture_output=True)
    if access.returncode:
        # Ansible uses 4 for an unreachable host. Authentication failures for a
        # missing dev account are expected on first setup; task/sudo failures are not.
        if access.returncode != 4:
            print(access.stdout + access.stderr, file=sys.stderr)
            return access.returncode
        validate_bootstrap_password(initial_password)

        print("Establishing development access through the initial administrator.", flush=True)
        bootstrap_env = {**env, "DEPLOY_BOOTSTRAP_PASSWORD": initial_password,
                         "SUDO_PASSWORD_HASH": password_hash(env["ANSIBLE_BECOME_PASS"])}
        bootstrap_env.pop("ANSIBLE_BECOME_PASS")
        if env.get("GITHUB_ACTIONS") == "true":
            # Runner masking commands do not appear as visible log entries.
            print("::add-mask::" + bootstrap_env["SUDO_PASSWORD_HASH"], flush=True)
        bootstrap = run_playbook(bootstrap_env, "bootstrap")
        if bootstrap.returncode:
            return bootstrap.returncode

    # The site play reopens and verifies dev access before making host changes.
    print("Configuring the server through development-user SSH key access.", flush=True)
    return run_playbook(env, "site").returncode


def prepare_inventory_environment(name, variables, environment, inputs=None):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
        raise ValueError("Inventory entries must use exact hostnames, without patterns or SSH options.")
    host_inputs = {**(inputs or {}), "server_address": variables.get("ansible_host", name)}
    env = prepare_environment({"inputs": host_inputs}, {
        **environment,
        "DEPLOY_SSH_PORT": str(variables.get("ansible_port", environment.get("DEPLOY_SSH_PORT") or "22")),
    })
    configured = json.loads(env["DEPLOY_INVENTORY"])["all"]["children"]["development"]["hosts"]["dev01"]
    host = {**variables, **configured,
            "ansible_user": configured["sudo_user"], "dev_host_user": configured["sudo_user"]}
    if inputs is None:
        host.pop("dev_host_bootstrap_user", None)
    host.pop("bootstrap_password_secret", None)
    env["DEPLOY_HOST"] = name
    env["DEPLOY_INVENTORY"] = json.dumps({"all": {"children": {"development": {"hosts": {name: host}}}}})
    return env


def update_inventory(environment):
    inventory = load_inventory()
    names = development_hosts(inventory)
    if not names:
        message = "No enabled servers in the development inventory; nothing to configure."
        print(f"::notice::{message}", flush=True)
        if environment.get("GITHUB_STEP_SUMMARY"):
            with open(environment["GITHUB_STEP_SUMMARY"], "a") as report:
                report.write(message + "\n")
        return 0
    hostvars = inventory.get("_meta", {}).get("hostvars", {})
    # Validate all targets and required settings before changing any server.
    targets = [(name, prepare_inventory_environment(name, hostvars.get(name, {}), environment)) for name in names]
    results = []
    for name, env in targets:
        print(f"Checking SSH and sudo access for {name}.", flush=True)
        access = run_playbook(env, "access", capture_output=True)
        if access.returncode:
            print(access.stdout + access.stderr, file=sys.stderr)
            # These are failed access tasks or unreachable hosts, respectively.
            # Syntax, inventory, and runner errors remain failures.
            if access.returncode in (2, 4):
                message = (f"Skipped {name}: development-account SSH/sudo access is unavailable. "
                           "Check access, or run Bootstrap VPS manually for first setup. Bootstrap credentials were not tried.")
                print(f"::warning::{message}", flush=True)
                results.append((name, "Skipped: SSH/sudo access unavailable"))
            else:
                print(f"::error::Access check could not run for {name} (exit {access.returncode}).", flush=True)
                results.append((name, "Failed: access check error"))
            continue

        print(f"Configuring {name} through existing development access.", flush=True)
        result = run_playbook(env, "site")
        if result.returncode:
            print(f"::error::Configuration failed for {name} (exit {result.returncode}).", flush=True)
            results.append((name, "Failed: configuration error"))
        else:
            results.append((name, "Updated"))

    summary = "### Inventory results\n\n| Server | Result |\n| --- | --- |\n"
    summary += "".join(f"| {name} | {result} |\n" for name, result in results)
    summary += "\nSkipped servers were left unchanged. First setup must be run manually.\n"
    print(summary, flush=True)
    if environment.get("GITHUB_STEP_SUMMARY"):
        with open(environment["GITHUB_STEP_SUMMARY"], "a") as report:
            report.write(summary)
    return int(any(result.startswith("Failed:") for _, result in results))


def main(*, update_only=False):
    try:
        if update_only:
            return update_inventory(dict(os.environ))
        with open(os.environ["GITHUB_EVENT_PATH"]) as event_file:
            event = json.load(event_file)
        inputs = workflow_inputs(event)
        name, variables = resolve_target(event)
        initial_password = inputs.get("bootstrap_password", "")
        env = prepare_inventory_environment(name, variables, dict(os.environ), inputs=inputs)
        return configure_server(env, initial_password)
    except (OSError, KeyError, json.JSONDecodeError, subprocess.TimeoutExpired):
        print("Could not prepare deployment inputs or run the deployment tools.", file=sys.stderr)
        return 1
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    if sys.argv[1:] not in ([], ["--update-inventory"]):
        raise SystemExit("Unsupported workflow runner argument.")
    raise SystemExit(main(update_only=bool(sys.argv[1:])))
