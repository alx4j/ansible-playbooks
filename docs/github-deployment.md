# Manual deployment from GitHub Actions

The **Deploy Ansible manually** workflow runs only through `workflow_dispatch`. Merge it into the default branch before using **Run workflow**. Pushes, pull requests, and merges do not deploy.

## 1. Configure the environment

Create an environment such as `development` in **Settings → Environments**. Restrict deployment branches to the default branch; the deployment job also skips other branches. Environment features depend on repository visibility and GitHub plan.

Save these settings once. The workflow builds its inventory from the run form; no inventory YAML is needed in GitHub.

| Name | GitHub setting | Value |
| --- | --- | --- |
| `ANSIBLE_KNOWN_HOSTS` | Variable | Independently verified OpenSSH `known_hosts` entries. |
| `ANSIBLE_SSH_PORT` | Optional variable | Existing SSH port; defaults to `22`. This does not change the server's listening port. |
| `ANSIBLE_SSH_PRIVATE_KEY` | Secret | Dedicated deployment key for GitHub runner → `dev` on the server. The workflow derives and installs its public key during bootstrap. Use a key without an interactive passphrase. |
| `DEV_PASSWORD` | Secret | Password for `dev` to use with sudo. The workflow derives its hash for account creation; routine runs use the same secret for sudo. |

Choose `DEV_PASSWORD` for a new account; if `dev` already exists, supply its current sudo password. Bootstrap preserves existing passwords. Updating the secret does not rotate an existing account's password. Use a single-line value of at most 256 UTF-8 bytes.

`DOPPLER_TOKEN` is reserved for later Doppler setup on the target. This workflow does not read it, fetch bootstrap credentials from Doppler, or install the token on the server yet.

If upgrading from the previous workflow, replace `DEV_PASSWORD_HASH` and `ANSIBLE_BECOME_PASSWORD` with `DEV_PASSWORD`. `ANSIBLE_INVENTORY`, `ANSIBLE_BOOTSTRAP_PASSWORD`, and `ANSIBLE_BOOTSTRAP_BECOME_PASSWORD` are no longer read by the workflow.

Obtain the server's SSH host key through a trusted channel such as the provider console. The entry must match the address entered in the form. For a nondefault SSH port, use `[hostname]:port` in `known_hosts` and set `ANSIBLE_SSH_PORT`. An unverified `ssh-keyscan` result does not establish trust.

The runner must be able to reach the VPS's SSH port. GitHub-hosted runners do not have a single fixed IP; network access must already be configured. Standard GitHub Actions usage and billing apply.

## 2. Establish access

The run form has three connection fields:

| Input | Value |
| --- | --- |
| `server_address` | Server IP address or hostname, without a username or port. |
| `bootstrap_user` | Existing administrator, such as `root` or `ubuntu`; defaults to `root`. Used only by `bootstrap`. |
| `bootstrap_password` | That administrator's initial SSH password. For a non-root account, the same password is used for sudo. Used only by `bootstrap`. |

`bootstrap_password` is an ordinary workflow input, not a GitHub secret or hidden password field. The first step masks it in logs, but it remains in the workflow event data. Disabling password SSH access later does not invalidate the password for sudo, `su`, or console login. Use this input with that visibility understood. Surrounding whitespace is rejected because Ansible's connection-password file reader strips it.

For a new VPS with password access:

1. Prepare a deployment key pair once and save its private half as `ANSIBLE_SSH_PRIVATE_KEY`. The initial account does not need this key installed. Save the chosen `DEV_PASSWORD` and verified host keys.
2. Select `bootstrap` and fill in the server address, initial username, and initial password. A non-root initial account must already have working sudo access with that password, or passwordless sudo.
3. Run with `apply` to create `dev`, grant sudo access, and install the deployment public key. Bootstrap leaves SSH policy unchanged.
4. Run `site` against the same server; leave the bootstrap fields unused. It connects as `dev` with the deployment key and uses `DEV_PASSWORD` for sudo. It verifies fresh login and sudo before hardening SSH, then verifies access again. The default policy disables password SSH authentication for all accounts and disables root SSH entirely.

When a bootstrap password is supplied, bootstrap uses it without falling back to the key. If it is blank, the deployment key must already be authorized for the initial account and non-root sudo must work without a password. For separate initial SSH and sudo passwords, use [local bootstrap](development.md#bootstrap-and-apply).

`site` and `maintenance` always use the deployment key and exclude the initial password. If `dev` already has working key and sudo access, skip bootstrap. Keep provider-console access available for recovery.

## 3. Check, review, and apply

1. Open **Actions → Deploy Ansible manually → Run workflow**.
2. Select the default branch, the prepared environment, a playbook, and the server address. Only bootstrap needs the initial account fields.
3. Select `check` and review the Ansible output.
4. Run again with the same inputs and `apply` to make changes. Each run records its commit; review intervening default-branch changes before applying.

`site` performs routine configuration. `maintenance` upgrades packages; autoremove/purge and reboots require explicit [configuration](configuration.md#common-host-overrides). For initial account creation, select `bootstrap` as described above.

Check mode still connects and performs read-only probes. On a fresh host it can fail when an earlier simulated installation has not created a required file or command. Investigate failures; check mode is not proof that apply will succeed, and checks must not be disabled to accommodate it.

The generated inventory contains one host, internally named `dev01`, with the `dev` account and deployment public key. Host patterns, SSH options, and multiple targets are rejected. The selected playbook must resolve to exactly that host; an empty match fails. Runs share one concurrency group: a running deployment is not canceled, but a newer pending run can replace an older pending run.

## Execution details

The first step reads the bootstrap password from `GITHUB_EVENT_PATH` and registers its log mask before checkout. It is never interpolated into shell code or a step's environment declaration. The workflow installs pinned dependencies through `scripts/setup` and runs `scripts/check` before passing the saved environment secrets to the deployment step.

`scripts/github-deploy.py` validates the form fields, derives the public key with `ssh-keygen`, and builds a temporary inventory. For bootstrap, it derives a salted SHA-512 crypt hash from `DEV_PASSWORD` through OpenSSL stdin and masks the hash. The development account uses `update_password: on_create`, so repeated bootstrap runs preserve its password.

`scripts/deploy` validates key formats, playbook syntax, and host selection; enforces SSH host-key checking; and invokes Ansible without interactive prompts or `--diff`. Password bootstrap uses Ansible's connection-password file and built-in `ssh_askpass` mechanism; it does not require `sshpass`. Passwords are never placed in command arguments. Credential files use mode `0600`; deployment files live in a `0700` temporary directory removed on exit and ordinary cancellation signals. Sudo authentication is passed through the environment. The initial password, event-file path, and account-creation hash are excluded from routine Ansible subprocesses; the original event file still belongs to the GitHub runner.

Codex/ClickUp login and Moshi pairing remain [interactive onboarding](configuration.md#account-onboarding). Controller-local configuration files must be available on the runner before setting their source paths; leave those settings empty to preserve existing files.

See [verification](development.md#verification) for automated checks and their limits. GitHub documents [manual inputs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatchinputs), [environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments), and [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
