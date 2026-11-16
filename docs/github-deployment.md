# Manual deployment from GitHub Actions

The **Deploy Ansible manually** workflow runs only through `workflow_dispatch`. Merge it into the default branch before using **Run workflow**. Pushes, pull requests, and merges do not deploy.

## 1. Configure the environment

Create an environment such as `development` in **Settings → Environments**. Restrict deployment branches to the default branch; the deployment job also skips other branches. Environment features depend on repository visibility and GitHub plan.

Copy [the inventory template](../inventories/example/hosts.yml), replace the address, and supply approved public keys as described in [configuration](configuration.md#inventory-and-shared-defaults). Store that YAML as `ANSIBLE_INVENTORY`. The same file works for local runs; shared settings and tool versions come from the role defaults.

| Name | GitHub setting | Value |
| --- | --- | --- |
| `ANSIBLE_INVENTORY` | Variable | Inventory YAML with connection details, public keys, and any host-specific overrides. |
| `ANSIBLE_KNOWN_HOSTS` | Variable | Independently verified OpenSSH `known_hosts` entries. |
| `ANSIBLE_BOOTSTRAP_PASSWORD` | Optional secret | Initial root SSH password. Used only for `bootstrap`; no private key is required for this connection. |
| `ANSIBLE_SSH_PRIVATE_KEY` | Secret for key access | Dedicated automation key for routine `dev` access, or bootstrap when no initial password is supplied. Must be usable without an interactive passphrase. |
| `ANSIBLE_BECOME_PASSWORD` | Optional secret | Sudo password for routine runs; omit for working passwordless sudo. |
| `DEV_PASSWORD_HASH` | Optional secret | New `dev` account's password hash for sudo; separate from the initial root password. See [sudo authentication](configuration.md#sudo-authentication). |

Keep passwords, hashes, and tokens out of the inventory and workflow inputs. For Doppler-managed values, use your established secure synchronization into GitHub environment secrets.

Obtain the server's SSH host key through a trusted channel such as the provider console. For a nondefault SSH port, use `[hostname]:port` in `known_hosts` and set both `ansible_port` and `dev_host_ssh_port`. An unverified `ssh-keyscan` result does not establish trust.

The runner must be able to reach the VPS's SSH port. GitHub-hosted runners do not have a single fixed IP; network access must already be configured. Standard GitHub Actions usage and billing apply.

## 2. Establish access

Choose credentials for the account used by the selected playbook:

| Playbook | SSH account and credentials |
| --- | --- |
| `bootstrap` | Root, using `ANSIBLE_BOOTSTRAP_PASSWORD` when set. Otherwise the supplied SSH key must already be authorized for root. Supply `DEV_PASSWORD_HASH` for the new development account, or explicitly choose passwordless sudo. |
| `site` / `maintenance` | The inventory account, normally `dev`. Its key and sudo authentication must already work. |

For a new VPS with password access:

1. Set `ANSIBLE_BOOTSTRAP_PASSWORD` to the provider's initial root password.
2. Prepare a deployment key pair once. Include its public half in `dev_host_authorized_keys` and save its private half as `ANSIBLE_SSH_PRIVATE_KEY` for subsequent runs. The initial root account does not need this key installed.
3. Run `bootstrap` with `apply` to create `dev`, grant sudo access, and install the approved keys. Bootstrap leaves SSH policy unchanged.
4. Run `site` using the deployment key. It verifies a fresh `dev` login and sudo before hardening SSH, then verifies access again. The default policy disables password SSH authentication for all accounts and disables root SSH entirely.

When both credentials are present, bootstrap uses the password without falling back to the key. `site` and `maintenance` always use the key and exclude the initial password. Remove the initial password from the environment once bootstrap is complete. If root login is already disabled and `dev` works, skip bootstrap.

Key-based bootstrap remains supported; use separate environments if its root key differs from the routine key. For an interactive controller, see [local bootstrap](development.md#bootstrap-and-apply). Keep provider-console access available for recovery.

## 3. Check, review, and apply

1. Open **Actions → Deploy Ansible manually → Run workflow**.
2. Select the default branch, the prepared environment, one exact inventory alias such as `dev01`, and a playbook.
3. Select `check` and review the Ansible output.
4. Run again with the same inputs and `apply` to make changes. Each run records its commit; review intervening default-branch changes before applying.

`site` performs routine configuration. `maintenance` upgrades packages; autoremove/purge and reboots require explicit [configuration](configuration.md#common-host-overrides). For initial account creation, select `bootstrap` as described above.

Check mode still connects and performs read-only probes. On a fresh host it can fail when an earlier simulated installation has not created a required file or command. Investigate failures; check mode is not proof that apply will succeed, and checks must not be disabled to accommodate it.

Host patterns and multiple targets are rejected. The selected playbook must resolve to exactly the requested host; an empty match fails. Runs share one concurrency group: a running deployment is not canceled, but a newer pending run can replace an older pending run.

## Execution details

The workflow installs pinned dependencies through `scripts/setup` and runs `scripts/check` before the deployment step receives secrets. The runner validates key formats, playbook syntax, and host selection; enforces SSH host-key checking; and invokes Ansible without interactive prompts or `--diff`. Password bootstrap uses Ansible's connection-password file and built-in `ssh_askpass` mechanism; it does not require `sshpass`. Passwords are never placed in command arguments. Temporary credential files use mode `0600` in a `0700` directory and are removed on exit and ordinary cancellation signals. Sudo authentication is passed through the environment. The initial password and account-creation hash are excluded from routine Ansible subprocesses.

Codex/ClickUp login and Moshi pairing remain [interactive onboarding](configuration.md#account-onboarding). Controller-local configuration files must be available on the runner before setting their source paths; leave those settings empty to preserve existing files.

See [verification](development.md#verification) for automated checks and their limits. GitHub documents [manual inputs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatchinputs), [environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments), and [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
