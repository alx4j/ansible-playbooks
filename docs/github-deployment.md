# Manual deployment from GitHub Actions

The **Deploy Ansible manually** workflow uses a GitHub-hosted Ubuntu runner as the Ansible controller. It runs only through `workflow_dispatch`; pushing, opening a PR, and merging do not deploy. The existing validation workflow remains automatic.

Merge the workflow into the default branch before using **Actions → Deploy Ansible manually → Run workflow**. Select the default branch, a GitHub environment, one inventory host, a playbook, and either `check` (the default) or `apply`. Other branches are skipped by the deployment job.

## Configure a deployment environment

Create an environment such as `development` in **Settings → Environments**. Restrict its deployment branches to the repository's default branch so feature branches cannot access its credentials. Environment feature availability depends on repository visibility and GitHub plan.

Set these environment variables and secrets through GitHub's settings or your established secret-manager synchronization. No credentials are included in this repository or supplied through workflow inputs.

| Name | GitHub setting | Purpose |
| --- | --- | --- |
| `ANSIBLE_INVENTORY` | Variable | Complete YAML inventory, including nonsecret role settings and approved public keys. |
| `ANSIBLE_KNOWN_HOSTS` | Variable | Verified OpenSSH `known_hosts` entries for the managed hosts. |
| `ANSIBLE_SSH_PRIVATE_KEY` | Secret | Dedicated SSH automation key authorized on the selected host. Must be usable without an interactive passphrase. |
| `ANSIBLE_BECOME_PASSWORD` | Optional secret | Sudo password for password-based escalation. Omit for working passwordless sudo. |
| `DEV_PASSWORD_HASH` | Optional secret | Account-creation hash for bootstrap; omitted from routine deployment subprocesses. |

Example `ANSIBLE_INVENTORY` (replace the documentation address and key):

```yaml
all:
  children:
    development:
      hosts:
        dev01:
          ansible_host: 192.0.2.10
          ansible_user: dev
      vars:
        dev_host_authorized_keys:
          - "ssh-ed25519 REPLACE_WITH_YOUR_PUBLIC_KEY workstation"
        dev_host_passwordless_sudo: false
```

Keep passwords, hashes, and tokens out of inventory variables. Use the environment secrets above. Existing Doppler-managed values can remain authoritative and be synchronized into these deployment secrets through your established secure process; this workflow does not enroll Doppler or copy credentials from a developer machine.

Obtain the server's SSH host key through an independently trusted channel, such as the provider console. For a nondefault SSH port, use `[hostname]:port` in `known_hosts` and set `ansible_port` and `dev_host_ssh_port` consistently in the inventory. The workflow never trusts an unverified `ssh-keyscan` result or disables host-key checking.

The runner needs network access to the VPS's SSH port. This workflow does not add firewall rules, provision a private-network connection, or install a self-hosted runner to establish that initial access. Use your existing network access design; GitHub-hosted runner addresses are not a fixed single IP.

## Choose a playbook

- **`bootstrap`** connects as root, so the supplied key must already be authorized for root. It creates the development account and approved keys, with a supplied password hash or an explicit passwordless-sudo choice. It does not restrict SSH. Initial password-only root access is not supported by this noninteractive workflow.
- **`site`** connects using the inventory account and verifies development-user login and sudo before configuring the host. It requires Python and working SSH access, just like a local controller run.
- **`maintenance`** upgrades packages. Autoremove/purge and reboots remain disabled unless enabled through their existing inventory variables.

The `host` input is one exact inventory alias, such as `dev01`. Wildcards, comma-separated targets, and command-line options are rejected. The selected playbook must resolve to exactly that host; an empty match fails instead of silently succeeding.

Check mode is the same partial simulation documented in the main README. It still connects to the host and performs read-only probes; a fresh host can fail later checks because simulated installations do not create files. It is not proof that an apply run will succeed.

## Execution and credential handling

The workflow installs pinned dependencies and runs repository checks before the deployment step receives secrets. It writes inventory, the SSH key, and trusted host keys into a private temporary directory, validates key formats, checks playbook syntax and host selection, and invokes Ansible without interactive prompts or `--diff`. Sudo authentication is passed through Ansible's supported environment variable, not command-line arguments. Temporary credential files are removed on exit, including ordinary failures and cancellation signals.

Deployments share a repository-wide concurrency group and do not cancel a running deployment. GitHub's default concurrency behavior retains at most one pending run; a newer pending run can replace an older one. Each run uses the selected default-branch commit and records it in the Actions run.

The workflow does not perform Codex login, ClickUp login, or Moshi pairing. Those remain separate account-onboarding operations. It also cannot use controller-local files referenced by `dev_host_codex_config_source` or `dev_host_codex_instructions_source` unless you deliberately make those files available to the runner; leave those settings empty to preserve existing files.

This change provides the workflow and setup instructions. It does not create a GitHub environment, install secrets, modify host access, or dispatch a deployment. Standard GitHub Actions usage/billing applies to manual runs.

## Validation

`scripts/check` includes offline deployment-runner regression tests. They exercise mode selection, exact-host enforcement, private file permissions, credential cleanup, and failure propagation with stubbed commands; they never connect to a VPS. Workflow syntax is additionally checked with actionlint during development.

References: [manual workflow inputs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatchinputs), [deployment environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments), [workflow concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
