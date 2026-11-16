# Manual deployment from GitHub Actions

**Bootstrap dev server** creates development access and configures the server in one run. **Maintain dev server** handles package upgrades separately. Both workflows are manual; merge them into the default branch before using **Run workflow**. Pushes, pull requests, and merges do not deploy.

## 1. Configure the environment

Use the `development` environment in **Settings → Environments**, creating it if needed. Both workflows always use this environment. Restrict deployment branches to the default branch; the jobs also skip other branches. Environment features depend on repository visibility and GitHub plan.

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
| `bootstrap_user` | Initial administrator, such as `root` or `ubuntu`; defaults to `root`. Used when `dev` key access is not established yet. |
| `bootstrap_password` | That administrator's initial SSH password. For a non-root account, the same password is used for sudo. Leave blank on later runs. |

`bootstrap_password` is an ordinary workflow input, not a GitHub secret or hidden password field. The first step masks it in logs, but it remains in the workflow event data. Disabling password SSH access later does not invalidate the password for sudo, `su`, or console login. Use this input with that visibility understood. Surrounding whitespace is rejected because Ansible's connection-password file reader strips it.

For a new VPS with password access:

1. Prepare a deployment key pair once and save its private half as `ANSIBLE_SSH_PRIVATE_KEY`. The initial account does not need this key installed. Save the chosen `DEV_PASSWORD` and verified host keys.
2. Open **Actions → Bootstrap dev server → Run workflow**. Select the default branch and enter the server address, initial username, and initial password. A non-root initial account must already have working sudo access with that password, or passwordless sudo.
3. Select `apply`. The workflow first checks `dev` key and sudo access. If the account cannot be reached, it uses the initial administrator to create `dev`, grant sudo access, and install the deployment public key.
4. The same run reconnects as `dev`, verifies key and sudo access, and configures the server. SSH hardening happens only after that verification, followed by another access check. The default policy disables password SSH authentication for all accounts and disables root SSH entirely.

When initial account creation is needed, a supplied password takes precedence over the deployment key. If it is blank, that key must already be authorized for the initial administrator and non-root sudo must work without a password. For separate initial SSH and sudo passwords, use [local bootstrap](development.md#bootstrap-and-apply).

On later runs, use the same workflow and server address. Working `dev` key and sudo access automatically skips initial account creation, even if old initial credentials were supplied. If the `dev` SSH connection succeeds but sudo fails, the run stops without trying the initial administrator. All connections enforce the saved SSH host keys. Keep provider-console access available for recovery.

## 3. Check, review, and apply

1. Open **Actions → Bootstrap dev server → Run workflow** for a server with working `dev` access.
2. Select the default branch and enter the server address. Initial credentials can be left blank; the saved `development` environment supplies the key and sudo password.
3. Select `check` to preview likely changes and review the Ansible output.
4. Run again with the same inputs and `apply` to make changes. Each run records its commit; review intervening default-branch changes before applying.

Check mode connects and performs read-only probes. It never creates `dev`: if development access is unavailable, it stops and explains that first setup requires `apply`. Even with working access, a simulated installation can leave later tasks without a required file or command. Investigate failures; check mode is not proof that apply will succeed, and checks must not be disabled to accommodate it.

The generated inventory contains one host, internally named `dev01`, with the `dev` account and deployment public key. Host patterns, SSH options, and multiple targets are rejected. Every stage must resolve to exactly that host; an empty match fails. Setup and maintenance share one concurrency group: a running deployment is not canceled, but a newer pending run can replace an older pending run.

## 4. Upgrade packages

Open **Actions → Maintain dev server → Run workflow**, select the default branch, and enter the server address. Choose `check` to preview package upgrades, then `apply` to perform them. This workflow requires existing `dev` key and sudo access and never uses initial administrator credentials. Autoremove/purge and reboots require explicit [configuration](configuration.md#common-host-overrides).

## Execution details

The setup workflow's first step reads the bootstrap password from `GITHUB_EVENT_PATH` and registers its log mask before checkout. It is never interpolated into shell code or a step's environment declaration. Both workflows install pinned dependencies through `scripts/setup` and run `scripts/check` before passing the saved environment secrets to the deployment step.

`scripts/github-deploy.py` validates the form fields, derives the public key with `ssh-keygen`, and builds a temporary inventory. It runs `access.yml` to verify `dev` login and sudo. In apply mode, an unreachable `dev` connection triggers `bootstrap.yml`; task and sudo failures stop the run. Both paths then run `site.yml`, which verifies fresh development access before configuration. Maintenance calls the same adapter with `--maintenance` to run only `maintenance.yml`.

Only initial account creation derives a salted SHA-512 crypt hash from `DEV_PASSWORD` through OpenSSL stdin and masks the hash. The development account uses `update_password: on_create`, so existing passwords are preserved. Initial SSH/sudo credentials are isolated to the account-creation stage; configuration uses the deployment key and `DEV_PASSWORD`.

`scripts/deploy` validates key formats, playbook syntax, and host selection; enforces SSH host-key checking; and invokes Ansible without interactive prompts or `--diff`. Each stage has its own SSH control directory so earlier connections cannot bypass credential or host-key checks. Password bootstrap uses Ansible's connection-password file and built-in `ssh_askpass` mechanism; it does not require `sshpass`. Passwords are never placed in command arguments. Credential files use mode `0600`; deployment files live in a `0700` temporary directory removed on exit and ordinary cancellation signals. Sudo authentication is passed through the environment. The initial password, event-file path, and account-creation hash are excluded from routine Ansible subprocesses; the original event file still belongs to the GitHub runner.

Codex/ClickUp login and Moshi pairing remain [interactive onboarding](configuration.md#account-onboarding). Controller-local configuration files must be available on the runner before setting their source paths; leave those settings empty to preserve existing files.

See [verification](development.md#verification) for automated checks and their limits. GitHub documents [manual inputs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatchinputs), [environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments), and [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
