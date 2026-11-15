# Development and local runs

## Prepare the controller

Use Python 3.13, Bash, and an SSH client. Install the OS's Python venv support if needed, then run from the repository root:

```bash
scripts/setup
mkdir -p inventories/local
cp -i inventories/example/hosts.yml inventories/local/hosts.yml
```

`scripts/setup` creates `.venv` and installs the pinned Python dependencies and Ansible collections. Both GitHub workflows use this same setup command.

Edit the local inventory using the [configuration guide](configuration.md#inventory-and-shared-defaults). Establish trust in the server's SSH host key before connecting; host-key checking remains enabled.

## Bootstrap and apply

For a new host, prepare [sudo authentication](configuration.md#sudo-authentication), then create the development account:

```bash
.venv/bin/ansible-playbook -i inventories/local/hosts.yml bootstrap.yml --limit dev01
```

Bootstrap explicitly connects as root, regardless of the inventory's routine user, and leaves SSH policy unchanged. Add `--ask-pass` if initial root SSH access requires a password. Skip bootstrap when `dev` already has working SSH and sudo access.

Review the selected hosts and predict routine changes:

```bash
.venv/bin/ansible-inventory -i inventories/local/hosts.yml --graph
.venv/bin/ansible-playbook -i inventories/local/hosts.yml site.yml --limit dev01 --check --ask-become-pass
```

Check mode is a partial simulation: read-only probes run, while simulated installations may leave later tasks without required files or commands. See [check-mode limitations](github-deployment.md#3-check-review-and-apply). Review failures before applying:

```bash
.venv/bin/ansible-playbook -i inventories/local/hosts.yml site.yml --limit dev01 --ask-become-pass
```

Omit `--ask-become-pass` only when sudo already works without a prompt. Keep provider-console access available for recovery when changing SSH or sudo.

For package maintenance:

```bash
.venv/bin/ansible-playbook -i inventories/local/hosts.yml maintenance.yml --limit dev01 --ask-become-pass
```

Autoremove/purge and reboots require explicit [host overrides](configuration.md#common-host-overrides).

## Verification

```bash
scripts/check
scripts/test-container
scripts/test-tools-container
```

| Check | Coverage |
| --- | --- |
| `scripts/check` | Unit tests, production ansible-lint profile, playbook syntax, host/task listings, inventory resolution, and Git whitespace. No managed-host connection. |
| `scripts/test-container` | Real account creation and sudo; preservation of unrelated groups/keys; SSH candidate validation with real `sshd`; zero changes on the second run. |
| `scripts/test-tools-container` | Actual Java, SDKMAN, Gradle, Codex, and Moshi installations, followed by a second run requiring zero changes. |

Deployment-runner unit tests use stub commands to check target selection, check/apply modes, credential permissions and cleanup, and failure propagation. They do not establish that a live deployment succeeds.

Container tests use Podman by default; set `CONTAINER_ENGINE=docker` to use Docker. They build a disposable Debian 13 image and download upstream packages. No production credentials are mounted or pairing performed. Containers are removed; the test image remains cached.

CI runs `scripts/check`, the focused account/SSH container tests, and actionlint for GitHub workflows. The actionlint version is pinned in [the validation workflow](../.github/workflows/validate.yml); its [official image](https://github.com/rhysd/actionlint/blob/v1.7.12/docs/usage.md#docker) includes ShellCheck and Pyflakes. To check workflows locally with that image:

```bash
podman run --rm -v "$PWD:/repo:ro" --workdir /repo docker.io/rhysd/actionlint:1.7.12
```

Docker can run the same command. Tool-installation tests remain explicit because they require larger upstream downloads. Systemd lifecycle, firewall enforcement, phone pairing, and fresh remote SSH access require an authorized representative VM run; container and structural checks do not verify them.
