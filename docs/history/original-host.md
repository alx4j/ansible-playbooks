# How the original development host was built

The playbooks grew out of a manually configured VPS. This archive preserves the original setup evidence, including a few choices that have since changed. For a new host, follow the [current setup guide](../../README.md#set-up-your-first-server).

The November 14, 2026 collection combined a user-supplied root history excerpt (12 entries, seven related to setup) with 147 lines of the development user's Bash history (36 setup-related entries retained). Neither source contained timestamps. The sections below group related work. Historical installer, maintenance, and authentication commands are included for reference.

## From the manual host to today's playbooks

| What the original user did | Where to do it today |
| --- | --- |
| Create a sudo account, install keys, and inspect SSH settings | [Bootstrap a server](../github-deployment.md#2-establish-access) |
| Install packages, configure APT and Mosh, and clear the login banner | [Host settings](../configuration.md#managed-state-and-ownership) |
| Install Java and Gradle and set the shell environment | [Java selection](../configuration.md#java-selection) |
| Install Codex, authorize ClickUp, and pair the phone | [First-login checklist](../first-login.md) |

## Recorded setup

### Create the account and check SSH

Root excerpt, entries 1–10. The administrator created the development account, installed sudo, edited the SSH drop-in, and ran syntax and effective-setting checks before reloading. The excerpt does not give the drop-in contents or check results.

<details>
<summary>Recorded commands</summary>

```bash
adduser dev
```

```bash
apt update
apt install -y sudo
usermod -aG sudo dev
```

```bash
nano /etc/ssh/sshd_config.d/00-hardening.conf
```

```bash
sshd -t
sshd -T | grep -E '^(permitrootlogin|passwordauthentication|kbdinteractiveauthentication) '
systemctl reload ssh
```

</details>

### Install updates

Development history, lines 5–9 and 15. The user refreshed APT, upgraded packages, and opened unattended-upgrade configuration. The final maintenance command also removes packages and purges their configuration. The responses to the configuration prompts were not recorded.

<details>
<summary>Recorded commands</summary>

```bash
sudo apt update
sudo apt upgrade -y
sudo apt install unattended-upgrades apt-listchanges
sudo dpkg-reconfigure unattended-upgrades
```

```bash
sudo apt update && sudo apt upgrade -y && sudo apt autoremove --purge -y
```

</details>

### Quiet the login banner

The static login banner at `/etc/motd` was confirmed empty during the original collection.

### Install container tools

Development history, line 10. The entry lists Podman and supporting packages. Container definitions and subordinate UID/GID assignments were outside the record.

<details>
<summary>Recorded commands</summary>

```bash
sudo apt install -y podman uidmap dbus-user-session podman-compose
```

</details>

### Install Java 25

Development history, lines 11–16 and 19–20. This host used the Azul APT repository and an AMD64-specific `JAVA_HOME`. The playbooks now use SDKMAN and jenv.

<details>
<summary>Recorded commands</summary>

```bash
sudo apt install -y curl gnupg ca-certificates
curl -fsSL https://repos.azul.com/azul-repo.key | sudo gpg --dearmor -o /usr/share/keyrings/azul.gpg
sudo chmod 644 /usr/share/keyrings/azul.gpg
echo "deb [signed-by=/usr/share/keyrings/azul.gpg] https://repos.azul.com/zulu/deb stable main" | sudo tee /etc/apt/sources.list.d/zulu.list
```

```bash
sudo apt update
sudo apt install -y zulu25-ca-jdk-headless
```

```bash
printf '%s\n' 'export JAVA_HOME=/usr/lib/jvm/zulu25-ca-amd64' 'export PATH="$JAVA_HOME/bin:$PATH"' | tee -a ~/.profile
source ~/.profile
```

</details>

### Add SDKMAN and Gradle

Development history, lines 24–27. These entries installed SDKMAN in the user account and requested Gradle. The record contains no version pins.

<details>
<summary>Recorded commands</summary>

```bash
sudo apt install -y curl zip unzip ca-certificates
curl -s https://get.sdkman.io | bash
source "$HOME/.sdkman/bin/sdkman-init.sh"
sdk install gradle
```

</details>

### Install Codex and start account authorization

Development history, lines 28, 33, 40, 97, and 102. The first editor command ran in `~/.codex/`. The uppercase `AGENTS.MD` spelling is preserved as entered. The record lacks the file contents, server registration, and authentication outcomes.

<details>
<summary>Recorded commands</summary>

```bash
curl -fsSL https://chatgpt.com/codex/install.sh | sh
```

```bash
codex login --device-auth
codex mcp login clickup --no-browser
```

```bash
nano AGENTS.MD
```

```bash
nano ~/.codex/config.toml
```

</details>

### Set up Moshi and its background service

Development history, lines 92–93 and 129–132. The user installed Moshi, enabled user lingering, attempted pairing, and installed and restarted its service. The pairing value is redacted. The saved commands establish neither the installer version nor the pairing outcome.

<details>
<summary>Recorded commands</summary>

```bash
curl -fsSL https://getmoshi.app/install.sh | sh
sudo loginctl enable-linger dev
```

```text
moshi-hook pair --token [REDACTED]
```

```bash
moshi-hook install
moshi-hook service restart
```

</details>

### Edit authorized SSH keys

Development history, lines 125 and 139. The editor ran from `~/.ssh/`. The key contents, resulting permissions, and effective user are unknown.

<details>
<summary>Recorded commands</summary>

```bash
nano authorized_keys
```

</details>

### Install Mosh and add its firewall rule

Development history, lines 134 and 136–137. The first rule attempt omitted the comment text. The block preserves the complete invocation. The history does not establish the firewall state or the result.

<details>
<summary>Recorded commands</summary>

```bash
sudo apt install mosh
sudo ufw allow 60000:61000/udp comment 'Mosh'
```

</details>

## Reading the evidence

The collection consolidated repeated commands, kept working-directory context for relative paths, and redacted the Moshi pairing token. Routine navigation, diagnostics, application sessions, cleanup, benchmarks, and unrelated project files were excluded. No commands were added to fill gaps or executed during collection. The archived commands were checked against the supplied histories. Apart from the empty static login banner, installation outcomes, configuration contents, and service health were unverified.
