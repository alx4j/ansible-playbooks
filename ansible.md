# Development host setup history

> Historical source snapshot. See [README.md](README.md) for the implemented playbooks, configuration inputs, and validation instructions.

Installation and configuration commands from the supplied root shell history and `dev`’s saved Bash history, grouped into blocks for future Ansible automation.

| Source | Details |
| --- | --- |
| Users | `root` and `dev` |
| Sources | User-supplied root history excerpt; `/home/dev/.bash_history` for `dev` |
| Collected | November 14, 2026 |
| Scope | Root: 12 supplied entries, including 7 setup entries; `dev`: 147 history lines reviewed, 36 setup-related entries retained after excluding benchmark tooling |
| Sensitive data | Moshi pairing token replaced with `[REDACTED]` |

> **Historical reference:** These commands record what was entered, not whether it succeeded or represents the current host state. This document is not a playbook or a script to run from top to bottom. No historical commands were executed during collection.

Repeated commands are consolidated below. Root references identify entries in the supplied excerpt; other line references identify `dev`’s history file at collection time. Neither source provides command timestamps; commands outside these sources are not covered. Section 2 summarizes intent instead of retaining editor commands.

## Contents

0. [Root bootstrap: user, sudo, and SSH](#0-root-bootstrap-user-sudo-and-ssh)
1. [System packages and automatic updates](#1-system-packages-and-automatic-updates)
2. [Login banner](#2-login-banner)
3. [Podman containers](#3-podman-containers)
4. [Java 25 and shell environment](#4-java-25-and-shell-environment)
5. [SDKMAN and Gradle](#5-sdkman-and-gradle)
6. [Codex and ClickUp authentication](#6-codex-and-clickup-authentication)
7. [Moshi and persistent user services](#7-moshi-and-persistent-user-services)
8. [SSH access](#8-ssh-access)
9. [Mosh and firewall access](#9-mosh-and-firewall-access)
10. [Ansible preparation notes](#10-ansible-preparation-notes)

## 0. Root bootstrap: user, sudo, and SSH

**Purpose:** Create the `dev` account, grant administrative access through `sudo`, and configure SSH access.

**Source:** User-supplied root history, entries 1–10. Commands in this block were entered as root.

### Create the development account

```bash
adduser dev
```

### Install sudo and grant access

```bash
apt update
apt install -y sudo
usermod -aG sudo dev
```

The repeated `usermod` entry is consolidated into one command. Interactive account-creation responses are not recorded.

### Configure SSH access

```bash
nano /etc/ssh/sshd_config.d/00-hardening.conf
```

The history then records these checks before reloading SSH:

```bash
sshd -t
sshd -T | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication) '
systemctl reload ssh
```

**Configuration gap:** The SSH configuration contents and check results were not supplied. The history shows inspection of root login, password authentication, and keyboard-interactive authentication settings, but does not establish their values or whether validation and reload succeeded.

## 1. System packages and automatic updates

**Purpose:** Refresh package indexes, upgrade packages, and configure unattended upgrades.

**History lines:** 5–9; the package-index refresh appears again at line 15 before Java installation.

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install unattended-upgrades apt-listchanges
sudo dpkg-reconfigure unattended-upgrades
```

A separate maintenance command also appears in the history:

```bash
sudo apt update && sudo apt upgrade -y && sudo apt autoremove --purge -y
```

**Configuration gap:** The responses to `dpkg-reconfigure` are not recorded. The maintenance command includes package removal and configuration purging; keep that distinct from initial package installation when defining automation.

## 2. Login banner

Keep `/etc/motd` empty to suppress the static login message.

## 3. Podman containers

**Purpose:** Install Podman and supporting packages.

**History line:** 10.

```bash
sudo apt install -y podman uidmap dbus-user-session podman-compose
```

**Configuration gap:** No container definitions, subordinate UID/GID settings, or container service configuration are recorded here.

## 4. Java 25 and shell environment

**Purpose:** Add the Azul package repository, install the headless Zulu 25 JDK, and configure the user's Java environment.

**History lines:** 11–16, 19–20.

### Repository prerequisites and signing key

```bash
sudo apt install -y curl gnupg ca-certificates
curl -fsSL https://repos.azul.com/azul-repo.key | sudo gpg --dearmor -o /usr/share/keyrings/azul.gpg
sudo chmod 644 /usr/share/keyrings/azul.gpg
echo "deb [signed-by=/usr/share/keyrings/azul.gpg] https://repos.azul.com/zulu/deb stable main" | sudo tee /etc/apt/sources.list.d/zulu.list
```

### JDK installation

```bash
sudo apt update
sudo apt install -y zulu25-ca-jdk-headless
```

### User environment

```bash
printf '%s\n' 'export JAVA_HOME=/usr/lib/jvm/zulu25-ca-amd64' 'export PATH="$JAVA_HOME/bin:$PATH"' | tee -a ~/.profile
source ~/.profile
```

**Automation note:** The recorded `JAVA_HOME` is specific to an AMD64 installation. Appending these lines repeatedly would duplicate them; future automation should manage a single environment definition. `source` applies the settings to the current shell.

## 5. SDKMAN and Gradle

**Purpose:** Install SDKMAN, initialize it in the current shell, and install Gradle.

**History lines:** 24–27.

```bash
sudo apt install -y curl zip unzip ca-certificates
curl -s https://get.sdkman.io | bash
source "$HOME/.sdkman/bin/sdkman-init.sh"
sdk install gradle
```

**Configuration gap:** Neither the SDKMAN version nor the Gradle version is pinned in the history.

## 6. Codex and ClickUp authentication

**Purpose:** Install Codex, authenticate it, and edit its local configuration.

**History lines:** 28, 33, 40, 97, 102.

### Installation

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

### Authentication

```bash
codex login --device-auth
codex mcp login clickup --no-browser
```

### Manual configuration

The first command was entered after `cd ~/.codex/`:

```bash
nano AGENTS.MD
```

The second uses an explicit home-relative path:

```bash
nano ~/.codex/config.toml
```

**Configuration gap:** The history does not capture the file contents, ClickUp MCP server registration, or authentication outcomes. `AGENTS.MD` preserves the exact capitalization recorded in history. Authentication requires a separate account setup step; no credentials belong in this document.

## 7. Moshi and persistent user services

**Purpose:** Install Moshi, enable lingering for `dev`, pair the hook, and install/restart its service.

**History lines:** 92–93, 129, 131–132.

### Installation and user service lifetime

```bash
curl -fsSL https://getmoshi.app/install.sh | sh
sudo loginctl enable-linger dev
```

### Hook pairing and service setup

The pairing command is a redacted historical example, not a usable credential:

```text
moshi-hook pair --token [REDACTED]
```

```bash
moshi-hook install
moshi-hook service restart
```

**Configuration gap:** The installer version, generated service configuration, and pairing outcome are not recorded. Lingering is an account-level setting for `dev`.

## 8. SSH access

**Purpose:** Edit the user's authorized SSH keys.

**History lines:** 125 and 139; duplicate invocations consolidated.

Both edits followed `cd ~/.ssh/`:

```bash
nano authorized_keys
```

**Configuration gap:** The history contains no key contents or resulting file permissions. The target was `~/.ssh/authorized_keys` in the shell's user context; the history alone does not prove that every command ran as `dev`, because a `su - root` invocation also appears earlier.

## 9. Mosh and firewall access

**Purpose:** Install Mosh and add its UDP port range to UFW.

**History lines:** 134, 136–137.

```bash
sudo apt install mosh
sudo ufw allow 60000:61000/udp comment 'Mosh'
```

**Recorded incomplete attempt:** Line 136 contains `sudo ufw allow 60000:61000/udp comment`, without comment text. The next entry supplies `'Mosh'`; only that complete form is included in the command block.

**Configuration gap:** These entries do not establish whether UFW was installed, enabled, or whether the rule was accepted.

## 10. Ansible preparation notes

The blocks above can guide a future playbook. The table identifies the recorded resources and the missing inputs; it does not establish a desired configuration beyond the history evidence.

| Block | Resources to manage | Inputs or decisions still needed |
| --- | --- | --- |
| Root bootstrap | `dev` account, sudo package and group membership, SSH configuration | Account-creation inputs and contents of `00-hardening.conf` |
| System updates | Packages and unattended-upgrade settings | Upgrade policy, reconfiguration answers, whether cleanup belongs in maintenance |
| Login banner | `/etc/motd` | Current file is empty; preserve that state when reproducing this configuration |
| Podman | Container packages | Any required rootless or container service configuration |
| Java | Signing key, APT repository, JDK, user environment | Target architecture and intended JDK version |
| Gradle | SDKMAN installation and Gradle version | Version selection and user initialization |
| Codex | CLI installation and user configuration files | Configuration contents, intended instruction filename, separate authentication |
| Moshi | Installation, lingering, hook service | Service configuration and secure pairing workflow |
| SSH | Authorized keys file | Approved public keys, owning account, permissions |
| Mosh | Package and UDP firewall rule | Existing firewall policy and intended rule scope |

### Selection and fidelity

- Included package installations, repository setup, environment changes, installer execution, authentication/pairing, configuration-file editor invocations, service setup, and firewall changes.
- Prepended the supplied root setup history; retained the SSH validation commands to show their position before the reload. Omitted the root excerpt's group inspection, application launch, and history listing.
- Excluded directory navigation/listings, file inspection, version checks, diagnostics, application launches/resumes, session management, benchmarks, document/archive downloads, temporary directory creation, and file cleanup.
- Excluded benchmark tooling installation at the user's request.
- Excluded `nano personas.md`: the history does not establish that this project document configures an installed tool or the host.
- Preserved recorded command arguments, apart from redacting the pairing token and normalizing redundant spaces. Working-directory context is explained beside relative paths.
- Retained both the standalone package maintenance commands and the combined maintenance command because the latter also removes packages. The APT refresh is shown in both relevant blocks for context.
- No commands were added to fill configuration gaps. Remote installers and repositories are recorded as historical URLs; their current availability and behavior were not checked.

### Verification

The document was checked against the saved `dev` history and supplied root excerpt for command coverage, references, and secret redaction. The current `/etc/motd` contents were inspected and confirmed empty. Root history was supplied by the user rather than read directly from root's history file. Installation results, other configuration file contents, authentication state, and service health were not verified. The recorded SSH checks were not executed during documentation. No playbook was created or applied.
