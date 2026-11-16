# Manual deployment from GitHub Actions

**Bootstrap dev server** creates development access and configures the server in one manual run. Pushes, pull requests, and merges do not deploy.

## 1. Configure the environment

Use the `development` environment in **Settings → Environments**, creating it if needed. The workflow always uses this environment. Restrict deployment branches to the default branch; the job also skips other branches. Environment features depend on repository visibility and GitHub plan.

Save these settings once. The workflow uses the [development inventory](../inventories/development/hosts.yml) to select saved bootstrap credentials for the server entered in the run form.

| Name | GitHub setting | Value |
| --- | --- | --- |
| `SUDO_USER` | Variable | Required non-root account name for routine SSH and sudo. Its value is kept outside the repository. |
| `ANSIBLE_KNOWN_HOSTS` | Variable | Independently verified OpenSSH `known_hosts` entries. |
| `ANSIBLE_SSH_PORT` | Optional variable | Existing SSH port; defaults to `22`. This does not change the server's listening port. |
| `ANSIBLE_SSH_PRIVATE_KEY` | Secret | Dedicated deployment key for GitHub runner → the configured account on the server. The workflow derives and installs its public key during bootstrap. Use a key without an interactive passphrase. |
| `SUDO_PASSWORD` | Secret | Password for the account named by `SUDO_USER` to use with sudo. The workflow derives its hash for account creation; routine runs use the same secret for sudo. |
| `BOOTSTRAP_DEV_ALX4J_COM_PASSWORD` | Secret | Initial SSH and sudo password for `debian` on `dev.alx4j.com`. Needed only when development access must be established. |

The workflow maps the GitHub `SUDO_USER` variable to the controller variable `DEPLOY_SUDO_USER`. Missing or invalid account names stop setup before any connection. GitHub variables are not automatically masked in logs.

Choose `SUDO_PASSWORD` for a new account; if the configured account already exists, supply its current sudo password. Bootstrap preserves existing passwords. Updating the secret does not rotate an existing account's password. Use a single-line value of at most 256 UTF-8 bytes.

For another registered server, add its entry directly under `development.hosts`, set `dev_host_bootstrap_user` and `bootstrap_password_secret`, and create that named secret in the same GitHub environment. Secret names use `BOOTSTRAP_<NAME>_PASSWORD`. The workflow injects only the secret selected for that run. It still configures one server per run; bulk runs and automatic deployment on merge are not enabled.

`DOPPLER_TOKEN` is reserved for later Doppler setup on the target. This workflow does not read it, fetch bootstrap credentials from Doppler, or install the token on the server yet.

Obtain the server's SSH host key through a trusted channel such as the provider console. The entry must match the resolved connection address, including an inventory `ansible_host` override when present. For a nondefault SSH port, use `[hostname]:port` in `known_hosts` and set `ANSIBLE_SSH_PORT`. An unverified `ssh-keyscan` result does not establish trust.

The runner must be able to reach the VPS's SSH port. GitHub-hosted runners do not have a single fixed IP; network access must already be configured. Standard GitHub Actions usage and billing apply.

## 2. Establish access

The run form has three connection fields:

| Input | Value |
| --- | --- |
| `server_address` | Server IP address, hostname, or inventory name, without a username or port. |
| `bootstrap_user` | Optional initial administrator override. Blank uses the inventory's administrator, or `root` for an unlisted server. |
| `bootstrap_password` | Optional initial password override. Blank selects the registered server's password secret. Non-root bootstrap uses the same password for sudo. |

`bootstrap_password` is an ordinary workflow input, not a GitHub secret or hidden password field. The first step masks it in logs, but it remains in the workflow event data. Disabling password SSH access later does not invalidate the password for sudo, `su`, or console login. Use this input with that visibility understood. Surrounding whitespace is rejected because Ansible's connection-password file reader strips it.

For a new VPS with password access:

1. Prepare a deployment key pair once and save its private half as `ANSIBLE_SSH_PRIVATE_KEY`. The initial account does not need this key installed. Save `SUDO_USER`, its `SUDO_PASSWORD`, and verified host keys in the `development` environment.
2. Open **Actions → Bootstrap dev server → Run workflow**. Select the default branch and enter the server address. For `dev.alx4j.com`, leave both initial credential fields blank: the inventory supplies `debian`, and GitHub supplies its saved password. For an unlisted server, enter its initial credentials. A non-root initial account must already have working sudo access with that password, or passwordless sudo.
3. Click **Run workflow** to perform setup. The workflow first checks development-account key and sudo access. If the account cannot be reached, it uses the initial administrator to create the configured account, grant sudo access, and install the deployment public key.
4. The same run reconnects as that account, verifies key and sudo access, and configures the server. SSH hardening happens only after that verification, followed by another access check. The default policy disables password SSH authentication for all accounts and disables root SSH entirely.

An explicit password overrides the saved secret. Changing the administrator to a different account disables use of that server's saved password, so supply an explicit password or use established initial key access. An unlisted server never receives a registered server's secret. Ambiguous inventory addresses fail before credentials are selected.

When bootstrap is needed and the selected saved secret is missing, setup stops with an error. For an unlisted server or an administrator override with no password, the deployment key must already be authorized for the initial administrator, and non-root sudo must work without a password. For separate initial SSH and sudo passwords, use [local bootstrap](development.md#bootstrap-and-apply).

On later runs, use the same workflow and server address. Working development-account key and sudo access automatically skips initial account creation, even if old initial credentials were supplied. If the development-account SSH connection succeeds but sudo fails, the run stops without trying the initial administrator. All connections enforce the saved SSH host keys. Keep provider-console access available for recovery.

## 3. Rerun setup

Open **Actions → Bootstrap dev server → Run workflow**, select the default branch, and enter the server address. Initial credentials can be left blank when development-account access works; the saved `development` environment supplies the key and sudo password. Clicking **Run workflow** applies the configuration directly. Review the server address and default-branch changes before running; each run records its commit.

The generated inventory contains one host, internally named `dev01`, with the configured `sudo_user` and deployment public key. Host patterns, SSH options, and multiple targets are rejected. Every stage must resolve to exactly that host; an empty match fails. Setup runs are serialized: a running deployment is not canceled, but a newer pending run can replace an older pending run.

## Execution details

The workflow's first step reads any explicit bootstrap password from `GITHUB_EVENT_PATH` and registers its log mask before checkout. It is never interpolated into shell code or a step's environment declaration. The workflow installs pinned dependencies through `scripts/setup` and runs `scripts/check`. A resolution step outputs only the selected secret's name; the deployment step then receives its value through GitHub's secrets context. Inventory resolution runs without deployment credentials in its subprocess environment.

`scripts/github-deploy.py` validates the form fields and required `SUDO_USER` account name, derives the public key with `ssh-keygen`, and builds a temporary inventory. It runs `access.yml` to verify the configured account's login and sudo. An unreachable development-account connection triggers `bootstrap.yml`; task and sudo failures stop the run. Both paths then run `site.yml`, which verifies fresh development access before configuration.

Only initial account creation derives a salted SHA-512 crypt hash from `SUDO_PASSWORD` through OpenSSL stdin and masks the hash. The development account uses `update_password: on_create`, so existing passwords are preserved. Initial SSH/sudo credentials are isolated to the account-creation stage; configuration uses the deployment key and `SUDO_PASSWORD`. Working development access does not require the initial password secret on subsequent runs.

`scripts/deploy` validates key formats, playbook syntax, and host selection; enforces SSH host-key checking; and invokes Ansible without interactive prompts or `--diff`. Each stage has its own SSH control directory so earlier connections cannot bypass credential or host-key checks. Password bootstrap uses Ansible's connection-password file and built-in `ssh_askpass` mechanism; it does not require `sshpass`. Passwords are never placed in command arguments. Credential files use mode `0600`; deployment files live in a `0700` temporary directory removed on exit and ordinary cancellation signals. Sudo authentication is passed through the environment. The initial password, event-file path, and account-creation hash are excluded from routine Ansible subprocesses; the original event file still belongs to the GitHub runner.

Codex/ClickUp login and Moshi pairing remain [interactive onboarding](configuration.md#account-onboarding). Controller-local configuration files must be available on the runner before setting their source paths; leave those settings empty to preserve existing files.

See [verification](development.md#verification) for automated checks and their limits. GitHub documents [manual inputs](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onworkflow_dispatchinputs), [environments](https://docs.github.com/en/actions/how-tos/deploy/configure-and-manage-deployments/manage-environments), and [concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency).
