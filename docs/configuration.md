# Configure your development host

Use inventory to choose settings for one server or a group. Role defaults supply the versions and policies shared across installations.

## Account onboarding

After bootstrap, follow the [first-login checklist](first-login.md). You can also jump to an individual step.

- <a name="authorize-clickup-mcp"></a>[Authorize ClickUp MCP](first-login.md#authorize-clickup-mcp)
- <a name="pair-moshi-with-the-qr-code"></a>[Pair Moshi with the QR code](first-login.md#pair-moshi-with-the-qr-code)
- <a name="approve-tokeneconomizer-hooks"></a>[Approve TokenEconomizer hooks](first-login.md#approve-tokeneconomizer-hooks)

## Inventory and shared defaults

The inventory lists your servers and their exceptions. Role defaults supply shared choices, including tool versions and SSH policy. Use the [development inventory](../inventories/development/hosts.yml) for Actions and an ignored `inventories/local/hosts.yml` for local runs. Start a local file from the [example inventory](../inventories/example/hosts.yml).

1. Put common settings under `development.vars`. Keep the `sudo_user` lookup. Actions supplies `DEPLOY_SUDO_USER` from its `SUDO_USER` variable. Local runs export it explicitly.
2. Add a name under `development.hosts`. Use `ansible_host` if the name differs from the connection address. Put exceptions for that server under its name.
3. For local runs, supply at least one approved **public** key. Replace the example below with your own. Include the deployment and workstation public keys you need. For Actions, the runner supplies its own deployment key and configured account.

```yaml
all:
  children:
    development:
      vars:
        sudo_user: "{{ lookup('ansible.builtin.env', 'DEPLOY_SUDO_USER', default=undef()) }}"
        dev_host_authorized_keys:
          - "ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY workstation"
      hosts:
        dev01:
          ansible_host: 192.0.2.10
          ansible_port: 2222
          dev_host_ssh_port: 2222
          dev_host_projects_dir: code
```

The example assumes SSH already listens on port `2222`. Host-specific settings win over role defaults. Lists replace their defaults. Ansible also accepts `group_vars` for deliberate shared overrides. Keep credentials, password hashes, and private keys out of inventories and tracked files.

## Sudo authentication

| Situation | What to supply |
| --- | --- |
| Fresh host via Actions | A separate initial admin account for bootstrap. If you use its password, the workflow locks it after verifying development access. Set `SUDO_PASSWORD` for the development account in the GitHub environment. |
| Existing development account | Its current sudo password. Bootstrap sets a salted password hash only when it creates an account. |
| Local bootstrap with password-based sudo | A SHA-512 crypt or yescrypt hash through `SUDO_PASSWORD_HASH`. If using Doppler, resolve the name from your integrations mapping and inject it with the wrapper. Keep it out of shell history and command arguments. |
| Routine local run | Use `--ask-become-pass` to enter the development user's sudo password. |
| Passwordless sudo for local runs | Set `dev_host_passwordless_sudo: true` to install the validated sudoers rule. Establish another working sudo method before turning it off. The GitHub workflow requires `SUDO_PASSWORD`. |

## Common host overrides

| What to change | Inventory setting |
| --- | --- |
| Development or initial account | `sudo_user` supplies `dev_host_user`. `dev_host_bootstrap_user` selects the initial local administrator, default `root`. |
| SSH port and firewall | Set `ansible_port` to the existing listener and `dev_host_ssh_port` to the same value. Set `dev_host_enable_firewall: true` to allow SSH on that port and activate UFW. |
| Automatic reboots | Set `dev_host_automatic_reboot: true` to allow unattended updates to reboot. Default is `false`. |
| Projects folder | Set `dev_host_projects_dir` on one host or `PROJECTS_DIR` for the deployment. |
| JDK and build tools | `dev_host_java_version`, `dev_host_jenv_java_alias`, `dev_host_jenv_revision`, and `dev_host_gradle_version`. |
| Go, Rust, Bun, Herdr, or Codex | Review the corresponding version and checksum settings together. [Tool update reference](updates.md#change-the-right-pins-together) |
| Codex configuration or instructions | `dev_host_codex_config_source` seeds a missing config. To manage `AGENTS.md` independently, disable `agent_toolkit_manage_instructions` and set `dev_host_codex_instructions_source`. |

Both Codex source paths default to empty. For custom files, use absolute paths in ignored `private/` on the controller. A GitHub runner needs its own copies. Ansible uses private permissions and suppresses file contents and diffs. [Host defaults](../roles/dev_host/defaults/main.yml) · [Toolkit ownership](agent-toolkit.md)

## Managed state and ownership

| On each run | What that means on the VPS |
| --- | --- |
| Access | Ansible checks key login and sudo before restricting SSH. It preserves unrelated authorized keys and groups. It validates SSH syntax and effective global settings, reloads, and tests a fresh connection. Defaults disable root, password, and keyboard-interactive SSH login. Review any client-specific `Match` rules on an existing host. |
| Updates and firewall | Daily APT timers apply the distribution's configured unattended upgrades. Automatic reboots default to off. Ansible adds Mosh's UDP rule (`60000:61000`). Enabling UFW is a separate choice. |
| Shell and services | The role clears the static `/etc/motd` banner, loads the development environment in Bash, enables user lingering, and starts `moshi-hook.service`. |
| Files to review on an existing host | Ansible owns the SSH drop-in `00-hardening.conf`, APT files `20auto-upgrades` and `52ansible-unattended-upgrades`, the Moshi user unit, and the optional `90-<user>-ansible` sudoers file. |

## Projects directory

Set the GitHub `PROJECTS_DIR` variable to a **folder name**, such as `code`. Blank uses `projects`. Use letters, digits, underscores, hyphens, and dots, with no leading dot. The destination must be a real directory inside the user's home. Ansible creates it with mode `0700`. In the remote shell, `PROJECTS_DIR` contains its full path.

```bash
cd "$PROJECTS_DIR"
```

Changing the name starts a separate directory and leaves the old projects and sync state in place. [Choose and archive projects](work-repositories.md)

## Java selection

SDKMAN installs the pinned Zulu JDK and Gradle. jenv selects the JDK and sets `JAVA_HOME`. In a new shell, inspect the default, then choose Java for a project from its checkout.

```bash
jenv versions
java -version
jenv local zulu-25
```

`jenv local` writes `.java-version` for that directory and its children. SDKMAN project switching is disabled. Existing project files, other registered JDKs, and old installations survive updates. Ansible owns the default jenv alias. To upgrade Java 25, change `dev_host_java_version` to a value from `sdk list java`. Projects using `zulu-25` follow that alias. Choose a new alias for a new major version. Give services an explicit Java environment and restart processes that need the new JDK.

## Go, Rust, and command lookup

New Bash shells can run `go`, `rustc`, `cargo`, `gradle`, and the JDK tools directly. For an existing shell or a noninteractive Bash script, load the same environment explicitly.

```bash
source "$HOME/.config/dev-environment/environment.sh"
go version
rustc --version
cargo --version
```

Go uses a verified, build-tested installation under `~/.local/share/go`. The `current` link selects it and `GOTOOLCHAIN=local` keeps that compiler in use. Rustup installs a minimal toolchain with Cargo and the standard library. Existing `rust-toolchain.toml` files and project overrides keep working. `build-essential` supplies the native linker. `~/go/bin` and `~/.cargo/bin` are on the development user's PATH.

Bash loads the shared environment from `.profile`, `.bashrc`, and existing Bash login files. Noninteractive SSH adds `~/.local/bin` early so Moshi can find Herdr. Give services and scheduled jobs an explicit environment.

### Keep Moshi sessions associated with their terminals

In interactive Bash, `codex` and `codex resume` use `--no-daemon` so Moshi can associate them with the right terminal. Use `command codex` when you want the shared server. [Using Herdr with Moshi](herdr.md)

## Versions and updates

Most tool versions and installer checksums live in the host defaults. Review a changed installer before changing its checksum. Moshi installs the current verified release on a fresh host and keeps its own updater. APT follows the distribution's package sources. [Update procedure](updates.md)
