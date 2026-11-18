# Run the playbooks locally

Use a local controller when you want to choose the target yourself, enter SSH and sudo passwords interactively, or test a change before opening a PR. For the GitHub-hosted path, use the [first-server walkthrough](github-deployment.md).

## Prepare the controller

1. Use Python 3.13, Bash, and an SSH client. Install your OS's Python venv support if needed. From the repository root, install the pinned dependencies and create a private inventory from the example.

   ```bash
   scripts/setup
   mkdir -p inventories/local
   cp -i inventories/example/hosts.yml inventories/local/hosts.yml
   ```

2. Edit `inventories/local/hosts.yml`. Replace the example addresses, remove unused servers, and add the **public** keys you want to authorize. Verify the host's SSH key through the provider console or another trusted channel before connecting. [Inventory example](configuration.md#inventory-and-shared-defaults)
3. Choose how the new account will use sudo and prepare its password hash if needed. If this is your own VPS, configure or disable the GitHub-backed roles before applying. [Sudo options](configuration.md#sudo-authentication) · [Integration choices](github-deployment.md#choose-what-to-install)

`scripts/setup` creates `.venv`, installs the Python requirements, and installs the pinned Ansible collections. CI and the GitHub deployment runner use the same setup.

## Bootstrap and apply

The examples select the inventory alias `dev01`. Substitute yours throughout. Keep the provider console available when changing access.

1. **Create the development account** if it lacks working SSH and sudo access. This play connects as `dev_host_bootstrap_user` (default `root`) and installs the approved public keys. Add `--ask-pass` for an initial SSH password and `--ask-become-pass` if the initial non-root administrator needs a sudo password. The account-creation play leaves SSH policy in place.

   ```bash
   .venv/bin/ansible-playbook -i inventories/local/hosts.yml bootstrap.yml --limit dev01
   ```

2. **Review the target and predicted changes.** Some read-only checks execute during check mode. A simulated installation may leave later checks without a file or command they need. Investigate any failure before applying.

   ```bash
   .venv/bin/ansible-inventory -i inventories/local/hosts.yml --graph
   .venv/bin/ansible-playbook -i inventories/local/hosts.yml site.yml --limit dev01 --check --ask-become-pass
   ```

3. **Configure the host.** With the default key-only policy, this play requires a working development-user SSH key and verifies sudo before applying host settings. Use an SSH agent or pass `--private-key` to select your key. Omit `--ask-become-pass` if the account already has passwordless sudo.

   ```bash
   .venv/bin/ansible-playbook -i inventories/local/hosts.yml site.yml --limit dev01 --ask-become-pass
   ```

4. **Start a fresh SSH session** using the development account and complete [account onboarding](first-login.md).

## Verification

Run `scripts/check` from the repository root after `scripts/setup`. Choose integration tests according to what changed.

| Command | What it exercises |
| --- | --- |
| `scripts/check` | Unit tests, Ansible lint, syntax, inventory, selected hosts and tasks, and Git whitespace. Runs on the controller. |
| `scripts/test-container` | Account creation, password and key transitions, sudo, host-key rejection, preservation of unrelated keys and groups, and repeated SSH setup in a disposable Debian 13 container. Includes repository synchronization over real SSH with fixture GitHub responses. |
| `scripts/test-tools-container` | Real tool installs, Go and Cargo builds, Java project selection, Bash PATH, project folders, Herdr discovery, and an unchanged second run. |
| `scripts/test-vm` | Full host setup on a disposable GitHub-hosted Ubuntu 24.04 VM, including systemd, SSH, UFW, APT timers, toolchains, and rerun behavior. This script is restricted to hosted CI. |

The container scripts default to Podman. Set `CONTAINER_ENGINE=docker` for Docker. They download upstream dependencies and remove their test containers afterward. The image remains cached. CI supplies a built image using `CONTAINER_TEST_IMAGE`. Leave that unset for a normal local build.

To check workflow syntax locally with the same actionlint image as CI, run the following from the repository root. Docker accepts the same arguments.

```bash
podman run --rm -v "$PWD:/repo:ro" --workdir /repo docker.io/rhysd/actionlint:1.7.12
```

### What a passing CI run covers

The [validation workflow](../.github/workflows/validate.yml) runs three jobs in parallel on PRs and pushes to `main`. The final `validate` job requires all three to pass.

- **Controller checks** cover access failures, credential cleanup, inventory selection, workflow inputs, and failure reporting. Unit tests use temporary repositories and synthetic API responses for discovery, checkout retention after topic changes, and recovery of older archives.
- **SSH container** tests the bootstrap-to-development-account transition over loopback SSH. Incorrect passwords or host keys fail. An automatic inventory run skips an inaccessible server and still updates an accessible one.
- **Full host and tools** bootstraps the VM, checks the working host, then applies again and requires zero changes. It checks service restart times, compiles example programs, verifies Herdr through noninteractive SSH, and exercises plugins with local fixtures. The Doppler tests cover encrypted storage, caller permissions, rotation, and restart using a synthetic token.

CI uses AMD64. Provider networking, phone pairing, live private GitHub or Doppler access, and paid model calls require their respective real environments. A validated push to the current `main` can start a deployment. A PR validates against disposable infrastructure. [Deployment behavior](github-deployment.md#3-apply-later-changes)
