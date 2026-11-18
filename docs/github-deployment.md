# Set up a VPS with GitHub Actions

Use **Bootstrap VPS** to configure one new server. After that, GitHub can apply reviewed changes to the servers you list in inventory. The runner supplies Ansible. To use this for your own servers, fork the repository or use a copy you administer, enable GitHub Actions, and have the provider's SSH and console access ready.

## Choose what to install

The default setup includes two integrations that use private GitHub access. Choose them before the first run.

| Feature | Default | For your own account |
| --- | --- | --- |
| Project workspaces | Clone `alx4j` repositories tagged `work` and open them in Herdr. | Set `work_repositories_owner` to your GitHub username and tag the repositories you want. Or set `work_repositories_enabled: false`. |
| Codex toolkit | Install plugins and global instructions from `alx4j/alx4j-agent-toolkit`. | Use a compatible toolkit repository and its owner, or set `agent_toolkit_enabled: false`. The Codex CLI remains available. |

Both integrations fetch `GITHUB_TOKEN` from the VPS's Doppler service and use SSH for Git. On a fresh server, configure Doppler if you enable either one. You can turn both off and still use the language tools, containers, Codex, Herdr, and Moshi. [GitHub permissions](work-repositories.md#github-access) · [Toolkit settings](agent-toolkit.md#inventory-settings) · [Doppler setup](doppler.md)

## 1. Configure the environment

1. In **Settings → Environments**, create `development`. Restrict deployment to `main`. Environment approvals also apply to automatic runs.
2. Add the settings below. GitHub distinguishes *variables* from *secrets*. Save them in this environment so the deployment job receives them.
3. Edit the [development inventory](../inventories/development/hosts.yml). Replace the empty `hosts: {}` with a `hosts:` mapping and add your server. Use its DNS name, or set `ansible_host` for an alias. Put host-specific integration choices here. An empty inventory opts out of automatic runs. [Inventory example](configuration.md#inventory-and-shared-defaults)

| GitHub setting | Kind | What to enter |
| --- | --- | --- |
| `SUDO_USER` | Variable | Non-root development account to create or reuse for daily SSH and sudo. |
| `SUDO_PASSWORD` | Secret | Sudo password for that account. Choose it for a new account or supply the current one. Use a single line of up to 256 UTF-8 bytes. |
| `SUDO_PRIVATE_KEY` | Secret | Dedicated, unencrypted deployment key. Bootstrap installs the public half for the development user. |
| `ANSIBLE_KNOWN_HOSTS` | Variable | Verified OpenSSH host-key entries for the addresses the runner connects to. Use `[hostname]:port` for a nondefault port. Verify the key through the provider console or another trusted channel. |
| `DOPPLER_TOKEN` | Secret | Read-only service token for the Doppler config this VPS should use. Needed when provisioning Doppler for the default integrations. |
| `ANSIBLE_SSH_PORT` | Optional variable | Existing SSH port, default `22`. The runner must be able to reach it. |
| `PROJECTS_DIR` | Optional variable | Folder name inside the development user's home, default `projects`. |
| `PROJECTS_TOPIC` | Optional variable | GitHub topic selecting project repositories, default `work`. |

For example, replace the empty `hosts` entry with this block to set up the development tools on your own server without GitHub project sync or the personal toolkit. Keep the surrounding `development.vars` mapping and replace the example address with yours.

```yaml
      hosts:
        my-vps:
          ansible_host: vps.example.com
          work_repositories_enabled: false
          agent_toolkit_enabled: false
```

Choose `my-vps` as the workflow target to apply those settings. If you want project sync, set `work_repositories_enabled: true`, add `work_repositories_owner` under that host, and configure the Doppler and GitHub credentials described above.

These account settings are shared across inventory servers. The development account keeps its existing sudo password. Changing `SUDO_PASSWORD` in GitHub alone cannot rotate the server password. Host variables can override the port and project settings. An unlisted manual target uses shared defaults, so list it first if you need your integration choices to apply.

## 2. Establish access

1. Open **Actions → Bootstrap VPS → Run workflow** and select `main`.
2. Set `server_address` to one hostname, IP address, or inventory alias. For a fresh VPS, set `bootstrap_user` to the initial administrator supplied by your provider. The default is `root`.
3. Leave `bootstrap_password` blank for key-based setup. For password-based setup, enter a temporary password for the initial administrator. Use a different account and password for `SUDO_USER` and `SUDO_PASSWORD`. **GitHub retains the form input in the workflow event data.** Use [local bootstrap](development.md#bootstrap-and-apply) to enter credentials outside GitHub or when the initial SSH and sudo passwords differ.
4. Run the workflow. If it needs the initial password, it records the authenticated account on the VPS and creates development access. It then opens a fresh SSH connection with the deployment key, verifies the development user can sudo, and locks the initial account's **local Linux password** before installing the rest of the tools.
5. SSH in with a key authorized for the development account and complete the [first-login checklist](first-login.md). Bootstrap verifies the runner’s key. Check your workstation key separately and keep provider rescue access available.

The locked password can no longer authenticate the initial account at an ordinary Linux console or for password-based sudo. The development user keeps their own sudo password. Your provider controls any separate rescue login.

If setup stops before the key and sudo checks succeed, the initial password can still work. Rerun **Bootstrap VPS**. Once development access works, enter just the server address. A verified run resumes any recorded lock. If setup stopped before recording the account, or if you entered a valid password on an already configured host and the workflow reported it unused, rotate that password yourself. When restoring password access to a locked initial account, set a **new** password because the old value is still in the event.

## 3. Apply later changes

Open a PR and review its validation results. After a merge, a successful **Validate playbooks** run for the current `main` push triggers **Bootstrap VPS**. The workflow processes inventory servers one at a time.

| Summary | What happened | What to do |
| --- | --- | --- |
| Updated | The development account had working SSH and sudo, and the playbook completed. | Use the updated host. |
| Skipped | The access check failed. The automatic run left that server alone and continued. | Check connectivity and credentials. Run the manual workflow for a new host. |
| Failed | The inputs, runner, or configuration step failed. | Fix the reported cause and rerun. |

A successful job can contain skipped servers. PR validation uses test environments. Only the current, successfully validated `main` push can trigger inventory deployment. Manual and automatic deployments share one concurrency group. An active deployment runs to completion, and a newer pending run may replace an older pending one. Standard GitHub Actions usage and billing apply.

## What runs on the controller

[The deployment workflow](../.github/workflows/deploy.yml) runs `scripts/setup` and `scripts/check`. The [deployment runner](../scripts/github-deploy.py) builds a temporary inventory for one server per stage. [The deploy script](../scripts/deploy) checks the target and syntax, verifies host keys, and uses a separate SSH connection for each stage. Credentials use private temporary files that are cleaned up on exit or ordinary cancellation. The Doppler service token only enters the routine host configuration stage. [Test coverage](development.md#verification)
