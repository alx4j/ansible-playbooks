# Development VPS playbooks

Ansible implementation of [the recorded setup history](ansible.md): a `dev` account, sudo and SSH access, automatic updates, Podman, Java 25, SDKMAN and Gradle, Codex, Moshi, and Mosh.

The playbooks target Debian/Ubuntu hosts with Python 3, OpenSSH, APT, and systemd already installed. AMD64 and ARM64 are supported by the configuration; container validation currently exercises AMD64 on Debian 13. No host is configured merely by cloning this repository.

## Layout

| Entry point | Responsibility |
| --- | --- |
| `bootstrap.yml` | Connect as root; create the account, sudo access, and approved keys. Does not restrict SSH. |
| `site.yml` | Connect as `dev`, verify a fresh login and sudo access, then configure the host. |
| `maintenance.yml` | Upgrade packages; optionally purge unused dependencies and reboot when required. |
| `roles/dev_host/` | Shared defaults, focused tasks, templates, validation helper, and service handlers. |
| `inventories/example/` | Documentation-only IP address and empty public-key list; copy before use. |

## Prepare the controller

Use Python 3.13 and an SSH client. Install the OS's Python venv support if necessary, then:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/ansible-galaxy collection install -r requirements.yml -p .ansible/collections
cp -r inventories/example inventories/local
```

Set your host address and SSH connection settings in `inventories/local/hosts.yml`. Put approved **public** keys in `inventories/local/group_vars/development.yml`:

```yaml
dev_host_authorized_keys:
  - "ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY workstation"
```

The placeholder above is not a usable key. The preflight requires at least one supplied key. Existing authorized keys and unrelated supplementary groups are preserved.

The local inventory, `private/`, environments, caches, and logs are ignored by Git. Keep passwords, hashes, tokens, private keys, and account files out of tracked files. Host-key checking stays enabled: establish trust in the server's SSH host key before connecting.

## Bootstrap a new host

Choose the account's sudo authentication deliberately:

- **Password-based sudo:** inject `DEV_PASSWORD_HASH` for account creation. The value must be a SHA-512 crypt or yescrypt hash, not plaintext. Existing account passwords are preserved (`update_password: on_create`).
- **Passwordless sudo:** explicitly set `dev_host_passwordless_sudo: true` in the local inventory. This installs a validated, project-owned sudoers file. The default is `false`.

For Doppler, first resolve the established secret name from the selected profile's `integrations.json`, then use the installed `doppler-secrets` wrapper to map that name to `DEV_PASSWORD_HASH` for the bootstrap process. Add the mapping through your normal secure setup if it does not exist. Do not put a hash or password in command arguments, the inventory, or shell history. This repository neither enrolls Doppler nor invents a secret name.

With authentication prepared:

```bash
.venv/bin/ansible-playbook -i inventories/local/hosts.yml bootstrap.yml --limit dev01
```

Use `--ask-pass` if initial root SSH access requires a password. Bootstrap explicitly selects root instead of the inventory's routine `dev` user. It leaves the existing SSH policy unchanged. If root login is already disabled and `dev` works, skip bootstrap and use `site.yml`.

For password-based sudo on subsequent runs, use Ansible's hidden terminal prompt:

```bash
.venv/bin/ansible-playbook -i inventories/local/hosts.yml site.yml --limit dev01 --ask-become-pass
```

Omit `--ask-become-pass` only when escalation is already available without a prompt. The site play verifies that the connected account matches `dev_host_user` and can become root before restricting SSH. Retain the provider console as the recovery path for access changes; a lost connection cannot be repaired by an Ansible rescue block.

## What the routine run manages

- Install the combined base package set in one APT task. `acl` supports switching to the unprivileged tool owner; UFW supplies the recorded firewall rule; `tmux` and Git support Moshi. Package presence is managed, not exact APT package revisions.
- Maintain the development account and approved keys; leave `/etc/motd` empty.
- Enable daily unattended updates and the distribution's APT timers. Preserve the distribution's existing allowed-origin policy. Automatic reboots default to disabled; this is an explicit conservative choice because the history did not record a reboot policy.
- Manage `zulu.list` and its repository-scoped public key, refresh metadata after repository changes, and install the Zulu 25 headless JDK. The owned source file replaces the historical key path cleanly instead of adding a conflicting repository entry.
- Initialize SDKMAN without letting its installer edit startup files. Install Gradle and manage one shared shell environment loaded from `.profile` and `.bashrc`; migrate the two exact Java exports recorded in the history.
- Install the selected Codex and Moshi releases under the development account. Run real version checks after installation. Existing broken installations fail visibly rather than being hidden by marker files.
- Install missing/outdated Moshi hooks for Codex, enable lingering, and manage `moshi-hook.service` through the user's systemd manager. Pairing is a separate account operation. An unpaired daemon is not proof of a working phone connection.
- Maintain the Mosh UDP rule for `60000:61000`. UFW is not enabled and its default policy is not changed unless `dev_host_enable_firewall: true`; that option first allows `dev_host_ssh_port`. The port variable describes existing SSH access and does not change sshd's listening port.
- Manage the three observed SSH settings: `PermitRootLogin no`, `PasswordAuthentication no`, and `KbdInteractiveAuthentication no`. Candidate validation expands the main configuration's Includes, checks syntax and effective global values, and rejects conflicts before replacing the live drop-in. Handlers validate again and reload; a fresh connection then verifies login and sudo. Match-specific policies require review for every relevant client context.

The role owns `00-hardening.conf`, `20auto-upgrades`, `52ansible-unattended-upgrades`, `zulu.list`, the optional `90-<user>-ansible` sudoers file, and the Moshi unit. Review existing contents before adopting this ownership on a different host. Do not change `dev_host_passwordless_sudo` to false until the account has another working sudo authentication method.

## Tool versions and configuration ownership

Defaults use versions observed on the source host: Gradle `9.8.2`, Codex `0.169.0`, and Moshi `0.4.57`. Override them in the local inventory. Review the installed CLI contracts when changing versions, particularly Moshi's JSON hook diagnostics.

Codex and SDKMAN installer downloads have reviewed SHA-256 pins. If the mutable upstream script changes, installation fails closed; review the new script and update the corresponding checksum deliberately. The SDKMAN installer snapshot reports `5.24.0`; an existing SDKMAN installation is preserved and configured rather than forcibly downgraded. Gradle uses SDKMAN checksum validation; Codex's reviewed installer verifies release assets. Moshi archive downloads require the release checksum manifest and never fall back to an unchecked download.

These choices do not freeze the operating system or every tool's own updater. Moshi and Codex may update themselves between Ansible runs; the next run reconciles the selected version. Decide whether to disable application updates through their supported configuration when strict version ownership is required.

Optional controller-side file inputs:

| Variable | Behavior |
| --- | --- |
| `dev_host_codex_config_source` | Seed `.codex/config.toml` only if absent. Thereafter the application and hook installer own it, avoiding repeated overwrites of hook settings. |
| `dev_host_codex_instructions_source` | Manage `.codex/AGENTS.md` from your supplied file. |

Use absolute paths inside ignored `private/` for these inputs. Both copies suppress output/diffs and use mode `0600`. Empty defaults preserve existing files. The history's uppercase `AGENTS.MD` is documented in `ansible.md`; the implementation uses the standard `AGENTS.md` filename. It does not delete the historical file.

## Account onboarding

Run these interactively as the development user after provisioning:

```bash
codex login --device-auth
codex mcp login clickup --no-browser
```

ClickUp login requires your MCP server registration in Codex configuration first; the history contains no registration to reproduce. No OAuth credentials are committed or requested by these playbooks.

Complete Moshi pairing through its supported app/CLI flow on your own terminal. The historical pairing token is deliberately omitted. The current `moshi-hook pair` exposes a command-line token flag; the playbook does not interpolate secrets into process arguments. For SSH/Mosh device setup, `moshi-hook host setup` offers the app's QR-based flow; review its proposed host setup changes separately. Re-pairing is not part of every convergence run.

## Maintenance

```bash
.venv/bin/ansible-playbook -i inventories/local/hosts.yml maintenance.yml --limit dev01 --ask-become-pass
```

Set `dev_host_maintenance_autoremove: true` for the historical remove-and-purge operation. Set `dev_host_maintenance_reboot: true` to reboot only when `/var/run/reboot-required` exists. Both default to false. The routine site play does not upgrade all packages or purge dependencies.

## Validation

```bash
scripts/check
scripts/test-container
scripts/test-tools-container
```

`scripts/check` runs unit tests, the production ansible-lint profile, syntax checks, host/task listings, inventory resolution, and Git whitespace checks. It does not connect to managed hosts.

Container tests use Podman by default (`CONTAINER_ENGINE=docker` also works). They build a disposable Debian 13 image. Account tests exercise real user creation and sudo, preserve unrelated groups/keys, and require zero changes on a second run. SSH tests use real `sshd` and reject malformed/conflicting candidates without changing the installed configuration. Tool tests install actual Java, SDKMAN, Gradle, Codex, and Moshi releases in the disposable container and check convergence. They download upstream packages; no production credentials are mounted or pairing performed. Containers are removed; the local test image remains cached.

CI runs structural checks and the focused account/SSH container tests. Tool installation tests are explicit because they involve larger upstream downloads. Tests do not prove systemd lifecycle behavior, firewall enforcement, phone pairing, or fresh remote SSH access; those require an authorized representative VM run.

After configuring a real inventory, review it before application:

```bash
.venv/bin/ansible-inventory -i inventories/local/hosts.yml --graph
.venv/bin/ansible-playbook -i inventories/local/hosts.yml site.yml --limit dev01 --check --ask-become-pass
```

Check mode is a partial simulation. Read-only identity and installed-version probes run; installers, pairing, and hook mutation do not. On a fresh host, later tasks may fail because an earlier simulated directory/package installation does not exist. Do not treat a check-mode result as a successful deployment or disable checks to accommodate that limitation.

## References

- [Azul APT installation](https://docs.azul.com/core/install/linux-ca-deb)
- [SDKMAN noninteractive installation](https://sdkman.io/install/)
- [Moshi hook installation and lifecycle](https://getmoshi.app/docs/hooks)
- [Ansible user accounts](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/user_module.html)
- [Ansible user services](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/systemd_service_module.html)
