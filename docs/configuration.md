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

Save local overrides as `inventories/local/hosts.yml`. Both manual and automatic GitHub runs preserve the selected inventory host's settings, including `ansible_port` and tool/project overrides. GitHub's configured development account and deployment key take precedence. An unlisted manual target uses shared role defaults and GitHub connection settings. Bootstrap credentials come only from the manual form. Keep shared defaults out of local copies so they follow repository updates. Ansible inventory `group_vars` files remain supported for deliberate overrides.

For example, this complete inventory selects an existing SSH port and a different projects folder for one server. Replace the address and public-key placeholder before using it; the playbook does not change the server's listening port.

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
| `dev_host_java_version` | Exact SDKMAN Zulu identifier; defaults to `25.0.5.0+8-zulu`. |
| `dev_host_jenv_java_alias` | jenv alias and account default; defaults to `zulu-25`. |
| `dev_host_jenv_revision` | Pinned jenv commit; currently the 0.6.0 release. |
| `dev_host_go_version`, `dev_host_go_checksums` | Exact Go release and official Linux archive SHA-256 values for AMD64 and ARM64. Change these together. |
| `dev_host_rust_version` | Exact default Rust toolchain, including `rustc` and Cargo. |
| `dev_host_rustup_version`, `dev_host_rustup_checksums` | Exact rustup installer release and architecture-specific SHA-256 values. Change these together. |
| `dev_host_projects_dir` | Projects folder name under the user's home; reads controller `PROJECTS_DIR`, defaulting to `projects`. |
| `dev_host_gradle_version`, `dev_host_codex_version` | Override the pinned tool versions. |
| `dev_host_herdr_version`, `dev_host_herdr_checksums` | Select Herdr and the matching Linux release checksums. |
| `dev_host_codex_config_source` | Seed `.codex/config.toml` only if absent. The application and hook installer subsequently own it. |
| `dev_host_codex_instructions_source` | Manage `.codex/AGENTS.md` from a supplied controller-side file. |

Both source-file settings default to empty, preserving existing files. Use absolute paths inside ignored `private/` for local runs. Copies use mode `0600` and suppress output/diffs. GitHub runners need their own copies of any referenced files. The historical uppercase `AGENTS.MD` file is not deleted.

## Managed state and ownership

The `site` play verifies development-user login and sudo before making SSH restrictions. It manages:

- A combined APT package set, including Podman, Mosh, UFW, Git, tmux, and `acl` for switching to the unprivileged tool owner. Package presence is managed, not exact APT revisions.
- The development account and approved keys, and an empty `/etc/motd` to suppress the static login message.
- Daily unattended updates and APT timers, preserving the distribution's allowed-origin policy. Routine runs do not upgrade all packages or purge dependencies.
- Zulu Java and Gradle through SDKMAN, Go from verified official archives, and Rust/Cargo through rustup, all under the development account. jenv selects Java. One shared shell environment is loaded from `.profile`, `.bashrc`, and any existing `.bash_profile` or `.bash_login`. Existing login customization is preserved.
- A private projects directory under the user's home. Existing contents are preserved; changing the configured name creates the new directory and leaves the old one in place.
- Codex, Moshi, and Codex hooks, with actual installed-version checks. User lingering and `moshi-hook.service` keep the hook daemon running, with its socket in the development account's runtime directory. An unpaired daemon does not establish a working phone connection.
- Herdr with a checksum-pinned binary, Bun, and the window-title plugin for readable Moshi session labels. PATH setup supports Moshi's noninteractive SSH discovery. See [Herdr and Moshi](herdr.md) to start workspaces and pair a phone.
- When `DOPPLER_TOKEN` is supplied, a separate [Doppler role](doppler.md) installs an isolated secret service, encrypted credential, and the Codex skill wrapper. It preserves existing private profile state outside the skill package.
- The Mosh UDP rule for `60000:61000`. UFW activation and its default policy remain unchanged unless firewall activation is explicitly enabled.
- `PermitRootLogin no`, `PasswordAuthentication no`, and `KbdInteractiveAuthentication no`. The SSH validator expands the main configuration's Includes and checks syntax and effective global values before replacing the drop-in. Handlers validate and reload; a fresh connection then verifies login and sudo. Review Match-specific policies for relevant client contexts.

The role owns `00-hardening.conf`, `20auto-upgrades`, `52ansible-unattended-upgrades`, the optional `90-<user>-ansible` sudoers file, and the Moshi unit. Review existing contents when adopting another host.

## Java selection

SDKMAN installs the pinned Zulu JDK in `~/.sdkman/candidates/java/<version>`. jenv registers that directory under `zulu-25` and selects it as the account's default. The shell loads SDKMAN first, then jenv; jenv's `export` plugin sets `JAVA_HOME`. SDKMAN automatic project switching is disabled so Java selection belongs to jenv.

```bash
jenv versions
java -version
echo "$JAVA_HOME"

cd my-project
jenv local zulu-25
```

`jenv local` writes `.java-version`, which selects Java inside that directory and its children. Other registered JDKs and project files are preserved. Ansible owns the configured default alias; changing the account default manually is reconciled on the next run. Use jenv to switch Java, and keep Java selection out of `.sdkmanrc`.

To upgrade within Java 25, change `dev_host_java_version` to an identifier available from `sdk list java`. The `zulu-25` alias follows the new installation, so projects using it follow the upgrade. For another major version, change the alias too. Older JDK installations are retained. Changing a shell's Java selection does not restart already-running Java processes.

Ansible tool commands use an explicit `dev_host_java_home` derived from the pinned SDKMAN version. Services and scheduled jobs must also receive an explicit Java environment; interactive shell initialization does not configure them.

## Go, Rust, and command lookup

New Bash login and interactive shells can run `java`, `javac`, `gradle`, `go`, `rustc`, and `cargo` directly. The shared environment also adds `~/go/bin` and `~/.cargo/bin` for tools installed with `go install` and `cargo install`.

Go is extracted and compiled against its standard library in a staging directory before moving into `~/.local/share/go/<version>/go`. A completion record identifies the verified archive, and a `current` symlink selects the managed release. Interrupted installs are retried even if a Go executable already exists. The shell sets `GOTOOLCHAIN=local` so Go uses that release and reports when a project requires a newer compiler. Upgrade the pinned version and both archive checksums together. Old version directories are retained.

Rustup installs a pinned minimal toolchain containing the compiler, standard library, and Cargo. `build-essential` supplies the native linker used by Cargo and Go's cgo support. Ansible manages the default toolchain and disables rustup's automatic self-update; existing toolchains, project `rust-toolchain.toml` files, and rustup overrides are preserved.

Existing sessions can load changes immediately, and noninteractive Bash scripts can initialize the same environment explicitly:

```bash
source "$HOME/.config/dev-environment/environment.sh"
go version
rustc --version
cargo --version
```

Services, scheduled jobs, and bare SSH commands need an explicit environment or a shell that loads this file. Shell startup configuration does not change system-wide PATH or already-running processes.

For Bash over SSH, a separate block at the start of `.bashrc` exposes `~/.local/bin`
before the noninteractive return. This lets Moshi discover Herdr and its own CLI
without loading the full SDK environment.

## Projects directory

Set the optional **`PROJECTS_DIR` variable in GitHub's `development` environment** to a folder name such as `code`. Local Ansible runs read the same controller environment variable. Unset or empty values use `projects`; a host inventory can override `dev_host_projects_dir`.

The name must start with a letter, digit, underscore, or hyphen and contain only letters, digits, underscores, hyphens, and dots. Paths, hidden names, and existing symlinks are rejected. Ansible creates the directory inside the managed user's home with mode `0700` and preserves its contents on reruns. In that user's shell, `PROJECTS_DIR` contains the full path:

```bash
cd "$PROJECTS_DIR"
```

## Versions and updates

Tool versions and installer checksums live in [role defaults](../roles/dev_host/defaults/main.yml). Codex and SDKMAN installer downloads use reviewed SHA-256 pins; a changed upstream script fails verification until its new content and checksum are reviewed. An existing SDKMAN installation is preserved rather than forcibly downgraded.

Follow the [update procedure](updates.md) when changing pins or reviewing Dependabot PRs.

Java and Gradle use SDKMAN checksum validation, jenv is pinned to a release commit, Go archives and the rustup installer use reviewed SHA-256 pins, and rustup verifies its toolchain downloads. Codex's installer verifies release assets, and Moshi requires a release checksum manifest. Broken installations fail visibly. Review CLI compatibility when changing versions, particularly Moshi's JSON hook diagnostics.

Moshi is not pinned: fresh installations use the current release, verified
against its checksum manifest. Existing installations and automatic-update
settings are preserved, so reruns do not downgrade Moshi after it updates itself.
APT packages and other application self-updaters are not frozen by the remaining
pins; the next Ansible run reconciles those selected tool versions.

## Account onboarding

After provisioning, run these interactively as the development user:

```bash
codex login --device-auth
codex mcp login clickup --no-browser
```

Register the ClickUp MCP server in Codex configuration before login. OAuth credentials are not managed by these playbooks.

Complete Moshi pairing in your terminal through its supported app/CLI flow. The playbook does not place pairing tokens in process arguments or repeat pairing during routine runs. `moshi-hook host setup` offers a QR-based flow; review its proposed host changes separately.

References: [Azul through SDKMAN](https://docs.azul.com/core/install/sdkman), [SDKMAN usage](https://sdkman.io/usage/), [jenv](https://github.com/jenv/jenv), [Moshi hooks](https://getmoshi.app/docs/hooks), [Ansible user accounts](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/user_module.html), and [user services](https://docs.ansible.com/projects/ansible/latest/collections/ansible/builtin/systemd_service_module.html).

Toolchain references: [Go downloads](https://go.dev/dl/), [Go toolchain selection](https://go.dev/doc/toolchain), and [rustup installation](https://rust-lang.github.io/rustup/installation/index.html).
