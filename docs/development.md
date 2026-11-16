# Development and local runs

## Prepare the controller

Use Python 3.13, Bash, and an SSH client. Install the OS's Python venv support if needed, then run from the repository root:

```bash
scripts/setup
mkdir -p inventories/local
cp -i inventories/example/hosts.yml inventories/local/hosts.yml
read -r -p "Development account: " DEPLOY_SUDO_USER
export DEPLOY_SUDO_USER
```

`scripts/setup` creates `.venv` and installs the pinned Python dependencies and Ansible collections. CI checks and the deployment workflow use the same setup command; container tests install their dependencies in the test image.

Edit the local inventory using the [configuration guide](configuration.md#inventory-and-shared-defaults). Establish trust in the server's SSH host key before connecting; host-key checking remains enabled.

## Bootstrap and apply

For a new host, prepare [sudo authentication](configuration.md#sudo-authentication), then create the development account:

```bash
.venv/bin/ansible-playbook -i inventories/local/hosts.yml bootstrap.yml --limit dev01
```

Bootstrap connects as `dev_host_bootstrap_user` (default `root`) and creates the account selected through `DEPLOY_SUDO_USER`, leaving SSH policy unchanged. The inventory maps that controller variable to `sudo_user`; routine playbooks connect as that account. Add `--ask-pass` when initial SSH access requires a password. For a non-root initial account, also add `--ask-become-pass` when sudo requires a password. Skip this local step when the development account already has working SSH and sudo access. The **Bootstrap dev server** workflow handles this transition and the following configuration automatically using the [initial connection inputs](github-deployment.md#2-establish-access).

Review the selected hosts and predict routine changes:

```bash
.venv/bin/ansible-inventory -i inventories/local/hosts.yml --graph
.venv/bin/ansible-playbook -i inventories/local/hosts.yml site.yml --limit dev01 --check --ask-become-pass
```

Local check mode is a partial simulation: read-only probes run, while simulated installations may leave later tasks without required files or commands. Investigate failures; check mode is not proof that apply will succeed, and checks must not be disabled to accommodate it. Review failures before applying:

```bash
.venv/bin/ansible-playbook -i inventories/local/hosts.yml site.yml --limit dev01 --ask-become-pass
```

Omit `--ask-become-pass` only when sudo already works without a prompt. Keep provider-console access available for recovery when changing SSH or sudo.

## Verification

```bash
scripts/check
scripts/test-container
scripts/test-tools-container
```

| Check | Coverage |
| --- | --- |
| `scripts/check` | Unit tests, production ansible-lint profile, playbook syntax, host/task listings, inventory resolution, and Git whitespace. No managed-host connection. |
| `scripts/test-container` | Real account creation and sudo; preservation of unrelated groups/keys; SSH candidate validation; zero changes on the second run; root and sudo-user password bootstrap followed by development-key login and sudo through loopback SSH. |
| `scripts/test-tools-container` | Actual Java, SDKMAN, Gradle, Codex, and Moshi installations, followed by a second run requiring zero changes. |

Deployment-runner unit tests check target selection, password/key selection, credential permissions and cleanup, and failure propagation. Workflow tests cover form validation, generated inventory, the automatic credential handoff, reruns, failure boundaries, and first-step masking. The container SSH test runs both deployment scripts against the production access playbook and a focused account fixture. It rejects incorrect login/sudo passwords and untrusted host keys, verifies that the access probe creates no account, and follows account creation with a fresh configured-account key/sudo connection in the same run. Reruns skip the initial administrator and converge with zero changes. These tests do not execute the full systemd-dependent playbooks.

Container tests use Podman by default; set `CONTAINER_ENGINE=docker` to use Docker. They build a disposable Debian 13 image and download upstream packages. No production credentials are mounted or pairing performed. Containers are removed; the test image remains cached.

CI runs checks and the focused account/SSH tests in parallel on pull requests and pushes to `main`. The final `validate` check requires both jobs to pass. GitHub Actions caches the test image's dependency layers; every run mounts the current source and executes all tests. CI passes the built image through `CONTAINER_TEST_IMAGE`; leave it unset locally to build the image automatically.

The actionlint version is pinned in [the validation workflow](../.github/workflows/validate.yml); its [official image](https://github.com/rhysd/actionlint/blob/v1.7.12/docs/usage.md#docker) includes ShellCheck and Pyflakes. To check workflows locally with that image:

```bash
podman run --rm -v "$PWD:/repo:ro" --workdir /repo docker.io/rhysd/actionlint:1.7.12
```

Docker can run the same command. Tool-installation tests remain explicit because they require larger upstream downloads. Systemd lifecycle, firewall enforcement, phone pairing, and fresh remote SSH access require an authorized representative VM run; container and structural checks do not verify them.
