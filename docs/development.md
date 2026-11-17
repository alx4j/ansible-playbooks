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

Bootstrap connects as `dev_host_bootstrap_user` (default `root`) and creates the account selected through `DEPLOY_SUDO_USER`, leaving SSH policy unchanged. The inventory maps that controller variable to `sudo_user`; routine playbooks connect as that account. Add `--ask-pass` when initial SSH access requires a password. For a non-root initial account, also add `--ask-become-pass` when sudo requires a password. Skip this local step when the development account already has working SSH and sudo access. The **Bootstrap VPS** workflow handles this transition and the following configuration automatically using the [initial connection inputs](github-deployment.md#2-establish-access).

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
| `scripts/test-tools-container` | Actual Java, SDKMAN, jenv, Gradle, Go, Rust/Cargo, Codex, and Moshi installations; shell PATH, real Go and Cargo builds, projects directory behavior, Java selection, and a second run requiring zero changes. |
| `scripts/test-vm` (CI only) | Complete bootstrap and host configuration over SSH on a fresh Ubuntu VM, tool builds, UFW, timers, and unchanged service state on rerun. |

Deployment-runner unit tests check target selection, password/key selection, credential permissions and cleanup, and failure propagation. Workflow tests cover form validation, generated inventory, credential handoff, automatic-run event restrictions, per-server skips, failure propagation, and first-step masking. The container SSH test runs both deployment scripts against the production access playbook and a focused account fixture. It rejects incorrect login/sudo passwords and untrusted host keys, verifies that the access probe creates no account, and follows account creation with a fresh configured-account key/sudo connection in the same run. Reruns skip the initial administrator and converge with zero changes. Automatic inventory tests verify that a missing account is never created, bad SSH/sudo access is skipped, and an accessible server still updates alongside an inaccessible one. These tests do not execute the full systemd-dependent playbooks.

Container tests use Podman by default; set `CONTAINER_ENGINE=docker` to use Docker. They build a disposable Debian 13 image and download upstream packages. No production credentials are mounted or pairing performed. Containers are removed; the test image remains cached. The tool test seeds an incomplete Go SDK with a working version command and requires a real repair before checking command lookup in login, interactive, and explicitly initialized noninteractive Bash shells. It compiles and runs Go and Cargo programs without external package dependencies, checks a custom `PROJECTS_DIR`, and rejects unsafe directory names and symlinks. An existing `.bash_profile` customization must survive while loading the managed environment. A separate Java fixture executable verifies project selection and return to the default without downloading a second JDK. Existing projects, toolchain configuration files, and unrelated jenv registrations must survive the second playbook run.

CI runs checks, focused account/SSH container tests, and a full-host Ubuntu 24.04 VM test in parallel on pull requests and pushes to `main`. The final `validate` check requires all three jobs to pass. Successful validation of the current `main` push triggers **Bootstrap VPS**; PR checks never configure inventory servers. GitHub Actions caches the test image's dependency layers; every run mounts the current source and executes all tests. CI passes the built image through `CONTAINER_TEST_IMAGE`; leave it unset locally to build the image automatically.

The actionlint version is pinned in [the validation workflow](../.github/workflows/validate.yml); its [official image](https://github.com/rhysd/actionlint/blob/v1.7.12/docs/usage.md#docker) includes ShellCheck and Pyflakes. To check workflows locally with that image:

```bash
podman run --rm -v "$PWD:/repo:ro" --workdir /repo docker.io/rhysd/actionlint:1.7.12
```

Docker can run the same command. The full-host job runs `scripts/test-vm`, which refuses to run outside a GitHub-hosted Linux runner. It creates disposable initial and development accounts, bootstraps over loopback SSH, applies the complete `site.yml`, runs the same tool-build and shell tests as the container suite, and applies the playbook again. It verifies UFW, APT timers, SSH policy, and the Moshi user service, including unchanged service reload/restart timestamps. Reviewed installer downloads are cached; checksum verification remains enabled. This job uses no deployment environment or production credentials.

The VM test covers real systemd and firewall configuration on Ubuntu AMD64. ARM64 execution, provider networking, and phone pairing remain outside these tests. Containers provide Debian AMD64 coverage. The hosted VM is discarded after the job; never adapt this test to an existing development or production host.

Both tool suites start a real Herdr session, discover its workspace through
Moshi, and check Moshi's context from inside a pane. The session's process and
workspace must survive an unchanged Ansible run. They verify the pinned title
plugin is enabled and executes through Bun when a workspace is renamed, before
and after convergence. The VM also checks Herdr and
Moshi discovery through a real noninteractive SSH connection. These checks do
not pair a phone or send notifications.

Repository-sync unit tests use real local Git repositories and synthetic GitHub
responses to cover pagination failures, access loss, identity changes, ownership,
archives, restores, interrupted operations, and active-work safeguards. The SSH
container job applies the production setup tasks twice, then exercises real SSH
cloning and pushing, rejects a mismatched host key, and checks archive/restore
convergence. GitHub API responses are fixtures; CI never registers real account
keys or accesses private repositories. The full-host VM disables that external
integration because its Doppler token is synthetic; the dedicated repository
tests are required through the existing checks and SSH jobs.

Toolkit unit tests cover package changes, cache repair, marketplace conflicts,
instruction and standalone-skill backups, and protection of local Git work. The
VM additionally runs the production checkout and installation tasks with a local
fixture repository and the real pinned Codex CLI. It verifies all four plugin
packages, updates from `main`, global instruction links, the plugin's Doppler
client, unchanged reruns, and preservation of an unfinished development branch.
The VM disables live toolkit GitHub access while running these local fixtures.

The disposable VM setup clears the hosted runner's global `XDG_CONFIG_HOME` and
`XDG_RUNTIME_DIR` assignments so fixture accounts and SSH logins use their own
home and runtime directories.
It also waits for initial APT update and upgrade jobs before recording service
timestamps, so background package upgrades do not overlap the convergence check.

Doppler unit tests exercise the Unix protocol, caller UID checks, selected-secret HTTP requests, safe errors, aliases, and command environment filtering using synthetic values. The VM job encrypts a synthetic token through the production role, checks root-only storage and service credentials, denies unrelated users, requires no service restart on an unchanged run, restarts without the controller token, and rotates the token. It does not use a real Doppler account or prove production API authentication.
