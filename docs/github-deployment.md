# Bootstrap VPS from GitHub Actions

**Bootstrap VPS** handles first setup manually and applies later configuration changes automatically after `main` passes validation. Pull requests run tests without contacting inventory servers.

## 1. Configure the environment

Use the `development` environment in **Settings → Environments**, creating it if needed. Restrict its allowed branches to `main`. Existing environment protection rules also apply to automatic runs.

Save these settings once:

| Name | GitHub setting | Value |
| --- | --- | --- |
| `SUDO_USER` | Variable | Required non-root account name for routine SSH and sudo. Its value stays outside the repository. |
| `SUDO_PASSWORD` | Secret | That account's sudo password. Bootstrap uses it when creating the account; existing passwords are preserved. |
| `ANSIBLE_SSH_PRIVATE_KEY` | Secret | Dedicated, unencrypted SSH key for runner → development account. Bootstrap derives and installs its public key. |
| `ANSIBLE_KNOWN_HOSTS` | Variable | Independently verified OpenSSH host-key entries for every managed server. |
| `ANSIBLE_SSH_PORT` | Optional variable | Existing SSH port; defaults to `22`. This does not change the server's listening port. |
| `PROJECTS_DIR` | Optional variable | Projects folder name inside the development user's home, such as `code`; defaults to `projects`. |
| `PROJECTS_TOPIC` | Optional variable | GitHub topic used to select repositories to synchronize; defaults to `work`. |
| `DOPPLER_TOKEN` | Optional secret | Read-only Doppler Service Token; enables encrypted credential storage and the local secret service for Codex skills. |

These account and key settings are shared across the inventory. GitHub maps `SUDO_USER` to the controller variable `DEPLOY_SUDO_USER`; variables are not automatically masked in logs. Missing required settings stop the run before any connection.

Choose `SUDO_PASSWORD` for a new account, or supply its current password when the account already exists. Changing the secret does not rotate existing passwords. Use a single-line value of at most 256 UTF-8 bytes.

The [development inventory](../inventories/development/hosts.yml) contains server names and a shared reference to `SUDO_USER`, with optional addresses, ports, and role settings. Add another server under `development.hosts`; no bootstrap username or per-server password secret is stored there. Existing `BOOTSTRAP_*_PASSWORD` secrets are unused and may be deleted. All hosts are currently commented out. To enable a host, change `hosts: {}` to `hosts:` and uncomment its entry. With no enabled hosts, automatic runs report that there is nothing to configure and make no connections.

Obtain each SSH host key through a trusted channel such as the provider console. The entry must match the connection address, including an `ansible_host` override. For a nondefault port, use `[hostname]:port` in `known_hosts`. An unverified `ssh-keyscan` result does not establish trust. The runner must be able to reach each server's SSH port.

When supplied, `DOPPLER_TOKEN` is delivered only to the routine `site` play and encrypted on the VPS. It is excluded from access probes, initial-user bootstrap, and generated inventory. See [Doppler setup and rotation](doppler.md). Omitting it leaves any existing service and credential unchanged. Standard GitHub Actions usage and billing apply.

[Work repository synchronization](work-repositories.md) is enabled by default.
It requires the local Doppler service and a `GITHUB_TOKEN` in its Doppler config
with access to your selected repositories. Fresh-server SSH enrollment also
needs **Git SSH keys: read/write** permission, or manual registration of the
server's generated public key. Disable `work_repositories_enabled` in inventory
if this host should not synchronize projects. The GitHub token stays in Doppler;
do not add another workflow secret for it.

The [Codex toolkit](agent-toolkit.md) uses the same access to update its managed
checkout and install plugins. Include `alx4j-agent-toolkit` in the GitHub token's
repository access. Toolkit installation is independent of project topics; add
the configured `PROJECTS_TOPIC` to that repo only when you also want an editable
working copy. Disable `agent_toolkit_enabled` in inventory to leave toolkit
installation unmanaged on a host.

## 2. Establish access

Open **Actions → Bootstrap VPS → Run workflow**, select `main`, and fill in:

| Input | Value |
| --- | --- |
| `server_address` | Server hostname, IP address, or inventory alias. |
| `bootstrap_user` | Initial administrator username, such as `debian`. Blank defaults to `root`. |
| `bootstrap_password` | Initial SSH password; also used for sudo when the initial administrator is not root. |

The initial administrator must already have sudo access. If its SSH and sudo passwords differ, use [local bootstrap](development.md#bootstrap-and-apply). Leaving the password blank requires the deployment key to be authorized for that initial account and noninteractive sudo to work.

`bootstrap_password` is an ordinary workflow input, not a hidden password field or GitHub secret. The first step masks it in logs, but it remains in the workflow event data. Disabling password SSH later does not invalidate it for sudo, `su`, or console login. Surrounding whitespace is rejected because Ansible strips it from connection-password files.

The manual run first checks development-account key and sudo access. If that connection is unavailable, it uses the initial administrator to create the configured account, grant sudo, and install the public key. It then reconnects through the development account and applies `site.yml`. Incorrect initial credentials fail the manual run. An existing development account with failing sudo also fails without attempting bootstrap.

Manual runs select one server. A registered alias or address uses that host's inventory port and configuration overrides, just as automatic runs do. Unlisted addresses use the shared defaults. Initial credentials always come from the form. Host patterns, shell options, and multiple targets are rejected. All connections enforce the saved host keys. Keep provider-console access available for recovery.

## 3. Apply later changes

Merge configuration changes into `main`. After **Validate playbooks** succeeds for that push, the same **Bootstrap VPS** workflow checks out the validated commit and processes inventory servers one at a time:

- Working development-account SSH and sudo access: apply `site.yml`.
- Missing account, rejected key, unavailable SSH, or failed sudo/access checks: warn, leave the server unchanged, and continue.
- Invalid inputs, runner errors, or failed configuration: fail the workflow. A configuration failure is never presented as a skipped server.

Automatic runs never try initial credentials, derive an account-creation password hash, or run `bootstrap.yml`. A warning means access is unavailable; it does not prove the account is absent. Check access, or run **Bootstrap VPS** manually for first setup.

The job summary lists every server as updated, skipped, or failed. A run with only skipped servers succeeds with warnings and lists every skip; no server is reported as updated. Failed or canceled validation, PR validation, and validation for an older commit do not start automatic configuration.

You can still rerun one server manually with just its address once development access works. Both paths share a concurrency group, so they cannot change servers simultaneously. Running setup is not canceled; a newer pending run can replace an older pending run.

## Execution details

Both paths install pinned dependencies through `scripts/setup` and run `scripts/check`. The deployment steps receive routine SSH/sudo credentials and the optional Doppler Service Token. Inventory resolution runs without credentials in its subprocess environment; automatic runs preserve resolved host-specific role settings and ports while enforcing the configured development account.

`scripts/github-deploy.py` creates a temporary inventory for exactly one server at each stage. `scripts/deploy` checks syntax and target selection, enforces host-key checking, and uses separate SSH control directories so earlier connections cannot bypass authentication. Passwords are never passed as command arguments. Credential files use mode `0600` inside a `0700` temporary directory, removed on exit and ordinary cancellation. Initial credentials and the account-creation hash are excluded from routine Ansible subprocesses.

Manual account creation derives a salted SHA-512 crypt hash from `SUDO_PASSWORD` through OpenSSL stdin. The account uses `update_password: on_create`, preserving existing passwords. Codex/ClickUp login and Moshi pairing remain [interactive onboarding](configuration.md#account-onboarding).

See [verification](development.md#verification) for test coverage and limits. GitHub documents [workflow completion triggers](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#workflow_run), [manual inputs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatchinputs), and [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
