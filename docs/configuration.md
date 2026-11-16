# Configuration

## Inventory and shared defaults

[Role defaults](../roles/dev_host/defaults/main.yml) are the shared source for package choices, tool versions, SSH policy, and optional features. Both local runs and GitHub Actions use them. Change a shared default in a reviewed commit; put host-specific overrides beside that host in the inventory.

Use YAML for server inventories. The [development inventory](../inventories/development/hosts.yml) lists managed servers. The [inventory template](../inventories/example/hosts.yml) shows two example server addresses. Shared settings belong under `development.vars`; each server belongs under `development.hosts`. A hostname used as the host entry's name is also its connection address unless `ansible_host` overrides it.

The inventories load `sudo_user` from the controller variable `DEPLOY_SUDO_USER`, keeping the account name outside tracked files. GitHub Actions supplies it from the `SUDO_USER` variable in the `development` environment; local runs must set it explicitly. There is no default account name. This setting controls account creation and routine SSH login; local bootstrap can set its initial administrator through `dev_host_bootstrap_user`.

For local runs, replace the example hosts and addresses, remove unused entries, and supply at least one approved **public** key:

```yaml
dev_host_authorized_keys:
  - "ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY workstation"
```

Place that setting under `development.vars` to share keys across the group, or under an individual host to override them. Include the public half of the routine deployment key along with your workstation keys. The placeholder is not a usable key. Existing authorized keys and unrelated supplementary groups are preserved.

Initial usernames and passwords are entered in the manual GitHub form. They are not stored in the inventory or in per-server GitHub secrets. Automatic runs only use existing development-account access.

Save local overrides as `inventories/local/hosts.yml`. Automatic GitHub runs read the tracked inventory, including host/group role settings and an optional `ansible_port`, then generate a one-host inventory for each server. GitHub's configured account and deployment key take precedence. Manual GitHub runs use the selected address, shared role defaults, and saved GitHub connection settings. Keep shared defaults out of local copies so they follow repository updates. Ansible inventory `group_vars` files remain supported for deliberate overrides.

Keep private connection details in the local inventory or GitHub settings. Keep passwords, hashes, tokens, private keys, and account files out of tracked files and inventory. `inventories/local/`, `private/`, environments, caches, and logs are ignored by Git.

## Sudo authentication

The initial account's password connects before the development account exists. GitHub reads it from the manual `bootstrap_password` input. Non-root bootstrap reuses that password for the initial account's sudo. It does not set the development account's password.

The default is password-based sudo. GitHub needs one environment secret, `SUDO_PASSWORD`: bootstrap derives a salted SHA-512 crypt hash for account creation; routine runs use the same password for sudo. Bootstrap preserves existing passwords through `update_password: on_create`. If the configured account already exists, use its current password. Changing the secret does not reset an existing account's password. SSH login still uses keys.

For local bootstrap, provide a SHA-512 crypt or yescrypt hash through `SUDO_PASSWORD_HASH`. Local routine runs can prompt for the original sudo password through `--ask-become-pass`.

For local inventories, choose passwordless sudo explicitly with `dev_host_passwordless_sudo: true`. This installs a validated, project-owned sudoers file. Do not switch it back to `false` until the account has another working sudo authentication method. The simplified GitHub workflow requires `SUDO_PASSWORD`.

For local Doppler use, resolve the established secret name from the selected profile's `integrations.json`, then use the installed `doppler-secrets` wrapper to map it to `SUDO_PASSWORD_HASH` for bootstrap. Add a missing mapping through your secure setup. Never put a hash or password in command arguments or shell history.

## Common host overrides

The inventory exposes `sudo_user` for the account name. Role inputs use the `dev_host_` prefix; their defaults are defined in the role. List-valued overrides replace the default list.

| Setting | Effect |
| --- | --- |
| `sudo_user` | Required development account for creation, routine SSH, and sudo; loaded from `DEPLOY_SUDO_USER`. |
| `dev_host_bootstrap_user` | Initial administrator used only by `bootstrap.yml`; defaults to `root`. An existing non-root account must already have sudo access. |
| `dev_host_user` | Role account setting; defaults to the required `sudo_user`. |
| `dev_host_passwordless_sudo` | Opt into passwordless sudo; disabled by default. |
| `dev_host_ssh_port` | Existing SSH port used by the firewall rule. Keep `ansible_port` consistent; this does not change sshd's listening port. |
| `dev_host_enable_firewall` | Enable UFW after allowing SSH; disabled by default. |
| `dev_host_automatic_reboot` | Allow unattended-update reboots; disabled by default. |
| `dev_host_gradle_version`, `dev_host_codex_version`, `dev_host_moshi_version` | Override the pinned tool versions. |
| `dev_host_codex_config_source` | Seed `.codex/config.toml` only if absent. The application and hook installer subsequently own it. |
| `dev_host_codex_instructions_source` | Manage `.codex/AGENTS.md` from a supplied controller-side file. |

Both source-file settings default to empty, preserving existing files. Use absolute paths inside ignored `private/` for local runs. Copies use mode `0600` and suppress output/diffs. GitHub runners need their own copies of any referenced files. The historical uppercase `AGENTS.MD` file is not deleted.

## Managed state and ownership

The `site` play verifies development-user login and sudo before making SSH restrictions. It manages:

- A combined APT package set, including Podman, Mosh, UFW, Git, tmux, and `acl` for switching to the unprivileged tool owner. Package presence is managed, not exact APT revisions.
- The development account and approved keys, and an empty `/etc/motd` to suppress the static login message.
- Daily unattended updates and APT timers, preserving the distribution's allowed-origin policy. Routine runs do not upgrade all packages or purge dependencies.
- Zulu Java through a repository-scoped signing key, plus SDKMAN and Gradle. One shared shell environment is loaded from `.profile` and `.bashrc`; the two historical Java exports are migrated.
- Codex, Moshi, and Codex hooks, with actual installed-version checks. User lingering and `moshi-hook.service` keep the hook daemon running. An unpaired daemon does not establish a working phone connection.
- The Mosh UDP rule for `60000:61000`. UFW activation and its default policy remain unchanged unless firewall activation is explicitly enabled.
- `PermitRootLogin no`, `PasswordAuthentication no`, and `KbdInteractiveAuthentication no`. The SSH validator expands the main configuration's Includes and checks syntax and effective global values before replacing the drop-in. Handlers validate and reload; a fresh connection then verifies login and sudo. Review Match-specific policies for relevant client contexts.

The role owns `00-hardening.conf`, `20auto-upgrades`, `52ansible-unattended-upgrades`, `zulu.list`, the optional `90-<user>-ansible` sudoers file, and the Moshi unit. Review existing contents when adopting another host. The owned Zulu source replaces the historical key path without adding a conflicting repository entry.

## Versions and updates

Tool versions and installer checksums live in [role defaults](../roles/dev_host/defaults/main.yml). Codex and SDKMAN installer downloads use reviewed SHA-256 pins; a changed upstream script fails verification until its new content and checksum are reviewed. An existing SDKMAN installation is preserved rather than forcibly downgraded.

Gradle uses SDKMAN checksum validation, Codex's installer verifies release assets, and Moshi requires a release checksum manifest. Broken installations fail visibly. Review CLI compatibility when changing versions, particularly Moshi's JSON hook diagnostics.

APT packages and application self-updaters are not frozen by these pins. The next Ansible run reconciles the selected tool versions. If strict version ownership is needed, configure application updaters through their supported settings.

## Account onboarding

After provisioning, run these interactively as the development user:

```bash
codex login --device-auth
codex mcp login clickup --no-browser
```

Register the ClickUp MCP server in Codex configuration before login. OAuth credentials are not managed by these playbooks.

Complete Moshi pairing in your terminal through its supported app/CLI flow. The playbook does not place pairing tokens in process arguments or repeat pairing during routine runs. `moshi-hook host setup` offers a QR-based flow; review its proposed host changes separately.

References: [Azul APT installation](https://docs.azul.com/core/install/linux-ca-deb), [SDKMAN installation](https://sdkman.io/install/), [Moshi hooks](https://getmoshi.app/docs/hooks), [Ansible user accounts](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/user_module.html), and [user services](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/systemd_service_module.html).
