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


def personal_public_key(value):
    if isinstance(value, str) and not value.strip():
        return None, None
    # Install only the key itself. A pasted comment is unnecessary in Ansible inventory.
    match = re.fullmatch(
        r"(ssh-ed25519|ssh-rsa|ecdsa-sha2-nistp(?:256|384|521)|sk-ssh-ed25519@openssh\.com|"
        r"sk-ecdsa-sha2-nistp256@openssh\.com)[ \t]+([A-Za-z0-9+/]+={0,2})(?:[ \t]+[^\r\n\0]*)?(?:\r?\n)?",
        value if isinstance(value, str) else "",
    )
    if not match:
        raise ValueError("Provide one OpenSSH public key in user_public_key.")
    key = " ".join(match.groups())
    check = subprocess.run(["ssh-keygen", "-l", "-E", "sha256", "-f", "-"], input=key + "\n", text=True,
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    if check.returncode:
        raise ValueError("Provide a valid OpenSSH public key in user_public_key.")
    return key, check.stdout.split()[1]


def validate_sudo_user(user):
    if not isinstance(user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", user) or user == "root":
        raise ValueError("Provide a valid non-root sudo username.")
    return user


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


def server_address(address):
    if not isinstance(address, str) or not address:
        raise ValueError("Provide the server IP address or hostname.")
    if "%" in address and not re.fullmatch(r"[0-9a-fA-F:.]+%[A-Za-z0-9_.-]+", address):
        raise ValueError("Provide a valid IPv6 scope identifier.")
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


def resolve_target(address, user):
    matches = [
        (name, variables) for name, variables in inventory_targets(load_inventory()).values()
        if address in (name, variables["ansible_host"])
    ]
    if len(matches) > 1:
        account_matches = [match for match in matches if match[1]["sudo_user"] == user]
        if len(account_matches) != 1:
            raise ValueError("The server address matches multiple inventory entries; use a unique hostname and sudo user.")
        return account_matches[0]
    return matches[0] if matches else ("dev01", {"ansible_host": server_address(address)})


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


def inventory_targets(inventory):
    hostvars = inventory.get("_meta", {}).get("hostvars", {})
    targets = {}
    for entry in development_hosts(inventory):
        user, separator, name = entry.partition("@")
        if not separator:
            raise ValueError("Use sudo_user@hostname for each development inventory entry.")
        validate_sudo_user(user)
        variables = hostvars.get(entry, {})
        # An alias may contain underscores when ansible_host provides the address.
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", name):
            server_address(name)
        address = server_address(variables.get("ansible_host", name))
        targets[entry] = (name, {**variables, "ansible_host": address, "sudo_user": user})
    return targets


def ssh_port(value):
    try:
        port = int(str(value))
    except ValueError:
        raise ValueError("ANSIBLE_SSH_PORT must be an integer from 1 to 65535.") from None
    if not 1 <= port <= 65535:
        raise ValueError("ANSIBLE_SSH_PORT must be an integer from 1 to 65535.")
    return port


def prepare_environment(event, environment):
    inputs = workflow_inputs(event)
    address = server_address(inputs.get("server_address"))

    bootstrap_user = inputs.get("bootstrap_user") or "root"
    if not isinstance(bootstrap_user, str) or not re.fullmatch(r"[a-z_][a-z0-9_-]*", bootstrap_user):
        raise ValueError("Provide a valid initial administrator username.")
    validate_bootstrap_password(inputs.get("bootstrap_password", ""))
    port = ssh_port(environment.get("DEPLOY_SSH_PORT") or "22")

    env = environment.copy()
    sudo_user = validate_sudo_user(inputs.get("sudo_user", "dev"))
    sudo_password = env.pop("SUDO_PASSWORD", "")
    if not sudo_password:
        raise ValueError("Set the SUDO_PASSWORD environment secret for development-user sudo.")
    initial_password = inputs.get("bootstrap_password")
    if initial_password and bootstrap_user == sudo_user:
        raise ValueError("Password bootstrap requires separate initial and development accounts. Use key access for a shared account.")
    if initial_password == sudo_password:
        raise ValueError("The bootstrap password was also supplied as SUDO_PASSWORD. Rotate the exposed password before retrying.")
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

    personal_key, _ = personal_public_key(inputs.get("user_public_key", ""))
    runner_key = public_key(env["DEPLOY_SSH_PRIVATE_KEY"])
    keys = [runner_key]
    if personal_key and personal_key.split() != runner_key.split()[:2]:
        keys.append(personal_key)
    host = {
        "ansible_host": address,
        "ansible_port": port,
        "sudo_user": sudo_user,
        "dev_host_bootstrap_user": bootstrap_user,
        "dev_host_ssh_port": port,
        "dev_host_authorized_keys": keys,
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

    elif initial_password:
        print("The initial password was unused because development access already works. "
              "If the form contained a valid password, rotate it on the server.", flush=True)

    # The site play reopens and verifies dev access before making host changes.
    print("Configuring the server through development-user SSH key access.", flush=True)
    return run_playbook(env, "site").returncode


def prepare_inventory_environment(name, variables, environment, inputs=None):
    # A temporary inventory contains one host. IPv6 colons are Ansible pattern syntax.
    alias = "dev01" if ":" in name else name
    host_inputs = {"sudo_user": variables["sudo_user"]} if inputs is None else inputs.copy()
    host_inputs["server_address"] = variables.get("ansible_host", name)
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
    env["DEPLOY_HOST"] = alias
    env["DEPLOY_INVENTORY"] = json.dumps({"all": {"children": {"development": {"hosts": {alias: host}}}}})
    return env


def update_inventory(environment):
    inventory = inventory_targets(load_inventory())
    if not inventory:
        message = "No enabled servers in the development inventory; nothing to configure."
        print(f"::notice::{message}", flush=True)
        if environment.get("GITHUB_STEP_SUMMARY"):
            with open(environment["GITHUB_STEP_SUMMARY"], "a") as report:
                report.write(message + "\n")
        return 0
    # Validate all targets and required settings before changing any server.
    targets = [(entry, prepare_inventory_environment(name, variables, environment))
               for entry, (name, variables) in inventory.items()]
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


def upload_public_key(inputs, environment):
    user = inputs.get("username")
    if not isinstance(user, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*\$?", user):
        raise ValueError("Provide the SSH username for the existing account.")
    key, fingerprint = personal_public_key(inputs.get("user_public_key", ""))
    if not key:
        raise ValueError("The upload requires a public key in user_public_key.")
    _, target = resolve_target(inputs.get("server_address"), user)
    host = {
        "ansible_host": target["ansible_host"],
        "ansible_user": user,
        "ansible_port": ssh_port(target.get("ansible_port", environment.get("DEPLOY_SSH_PORT") or "22")),
        "user_public_key": key,
        "user_key_fingerprint": fingerprint,
    }
    env = {name: environment[name] for name in (
        "PATH", "HOME", "LANG", "RUNNER_TEMP", "ANSIBLE_CONFIG", "ANSIBLE_COLLECTIONS_PATH",
        "ANSIBLE_LOCAL_TEMP", "DEPLOY_SSH_PRIVATE_KEY", "DEPLOY_KNOWN_HOSTS",
    ) if name in environment}
    env["DEPLOY_HOST"] = "upload_target"
    env["DEPLOY_INVENTORY"] = json.dumps({"development": {"hosts": {"upload_target": host}}})
    return run_playbook(env, "upload-public-key").returncode


def main(*, update_only=False, upload_key=False):
    try:
        if update_only:
            return update_inventory(dict(os.environ))
        with open(os.environ["GITHUB_EVENT_PATH"]) as event_file:
            event = json.load(event_file)
        inputs = workflow_inputs(event)
        if upload_key:
            return upload_public_key(inputs, os.environ)
        name, variables = resolve_target(inputs.get("server_address"), validate_sudo_user(inputs.get("sudo_user", "dev")))
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
    args = sys.argv[1:]
    if args not in ([], ["--update-inventory"], ["--upload-public-key"]):
        raise SystemExit("Unsupported workflow runner argument.")
    raise SystemExit(main(update_only=args == ["--update-inventory"], upload_key=args == ["--upload-public-key"]))
