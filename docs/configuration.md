# Configuration

## Inventory and shared defaults

[Role defaults](../roles/dev_host/defaults/main.yml) are the shared source for package choices, tool versions, SSH policy, and optional features. Both local runs and GitHub Actions use them. Change a shared default in a reviewed commit; put host-specific overrides beside that host in the inventory.

Use [the inventory template](../inventories/example/hosts.yml) for both controllers. Replace the documentation address and supply at least one approved **public** key:

```yaml
dev_host_authorized_keys:
  - "ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY workstation"
```

Place that setting under `dev01`, beside `ansible_host` and `ansible_user`. Include the public half of the routine deployment key along with your workstation keys. The placeholder is not a usable key. Existing authorized keys and unrelated supplementary groups are preserved.

For local runs, save the completed file as `inventories/local/hosts.yml`. For Actions, use its contents as the environment's `ANSIBLE_INVENTORY` variable. Keep shared defaults out of these copies so they follow repository updates. Existing Ansible inventory `group_vars` files remain supported for deliberate overrides.

Keep private connection details in the local inventory or GitHub settings. Keep passwords, hashes, tokens, private keys, and account files out of tracked files and inventory. `inventories/local/`, `private/`, environments, caches, and logs are ignored by Git.

## Sudo authentication

The initial root SSH password (`ANSIBLE_BOOTSTRAP_PASSWORD` in GitHub) is only for connecting before `dev` exists. It does not set the development account's password or authorize sudo for that account.

The default is password-based sudo. For a new account, inject `DEV_PASSWORD_HASH` containing a SHA-512 crypt or yescrypt hash. Bootstrap preserves existing passwords through `update_password: on_create`. Subsequent runs need the corresponding sudo password through GitHub's `ANSIBLE_BECOME_PASSWORD` secret or the local `--ask-become-pass` prompt.

To choose passwordless sudo explicitly, set `dev_host_passwordless_sudo: true`. This installs a validated, project-owned sudoers file. Do not switch it back to `false` until the account has another working sudo authentication method.

For local Doppler use, resolve the established secret name from the selected profile's `integrations.json`, then use the installed `doppler-secrets` wrapper to map it to `DEV_PASSWORD_HASH` for bootstrap. Add a missing mapping through your secure setup. Never put a hash or password in command arguments or shell history.

## Common host overrides

All role inputs use the `dev_host_` prefix. The defaults file defines their current values; list-valued overrides replace the default list.

| Setting | Effect |
| --- | --- |
| `dev_host_user` | Development account; defaults to `dev`. Keep routine `ansible_user` consistent with it. |
| `dev_host_passwordless_sudo` | Opt into passwordless sudo; disabled by default. |
| `dev_host_ssh_port` | Existing SSH port used by the firewall rule. Keep `ansible_port` consistent; this does not change sshd's listening port. |
| `dev_host_enable_firewall` | Enable UFW after allowing SSH; disabled by default. |
| `dev_host_automatic_reboot` | Allow unattended-update reboots; disabled by default. |
| `dev_host_maintenance_autoremove` | Remove and purge unused dependencies during maintenance; disabled by default. |
| `dev_host_maintenance_reboot` | Reboot during maintenance when `/var/run/reboot-required` exists; disabled by default. |
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
