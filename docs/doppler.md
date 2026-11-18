# Use Doppler secrets from a terminal

Let a program on the VPS use a GitHub, Notion, or other application credential without putting its value in a shell command. A local service holds a read-only Doppler Service Token. The development user requests secret names through a wrapper that starts one trusted program with the selected values.

## 1. Connect the VPS to Doppler

1. Choose a Doppler config containing the application credentials this server should use. Any process running as the development user can request ordinary secret names from that config. Scope it to that account's work.
2. Create a read-only Doppler Service Token for the config and save it as the `DOPPLER_TOKEN` secret in GitHub's `development` environment.
3. Run **Bootstrap VPS** for the host. The role encrypts the token on the VPS, starts the service, and verifies the local connection. It needs neither a Doppler CLI login nor a TPM. Omitting the token or setting `doppler_secrets_enabled: false` leaves an existing service in place.

On a fresh VPS, the default toolkit and project sync both need Doppler. To set up the host without either integration, set `agent_toolkit_enabled: false` and `work_repositories_enabled: false` in the inventory. On later runs you can omit the token when the existing service answers the development user's offline check.

## 2. Check the connection

Run these from the development user's SSH, Herdr, or tmux shell. Use a secret name your task needs. The example assumes `GITHUB_TOKEN` is configured.

```bash
python3 /usr/local/lib/doppler-secrets/client.py doctor --offline
python3 /usr/local/lib/doppler-secrets/client.py check --require GITHUB_TOKEN
```

| Result | What it tells you |
| --- | --- |
| Offline check succeeds | The local service answered this user's request. |
| Selected secret check succeeds | The service fetched a nonempty value under that name. |
| An authorized read succeeds in the application | The application accepted that credential for that operation. |

If only the fetch fails, check the exact name, the service token's scope or expiry, and connectivity to Doppler. The service suppresses detailed upstream errors to protect secret values. Use `systemctl status doppler-secrets.service doppler-secrets.socket` for service status. Avoid printing credential files or environments.

## 3. Run a command with a selected secret

Use `--require NAME` when the application expects the same environment variable name, or `--map SOURCE=TARGET` to rename it. Replace the example script with a trusted program on the VPS.

```bash
python3 /usr/local/lib/doppler-secrets/client.py run \
  --map NOTION_API_TOKEN=NOTION_TOKEN \
  --clean-env --inherit PATH --inherit HOME -- \
  /usr/bin/node /path/to/trusted-client.mjs
```

The wrapper fetches before starting the program and stops on failure. `--clean-env` keeps only the selected secrets and explicitly inherited variables. Without it, ordinary ambient variables pass through. Doppler runtime variables are stripped. The program keeps its stdin, working directory, umask, signals, and exit status. Its output goes straight to the terminal, so use a client that keeps values out of arguments, tracing, and logs.

Automation uses the stable client path above. Codex uses the wrapper bundled with the toolkit's `common` plugin, or the standalone skill at `~/.codex/skills/doppler-secrets` when toolkit management is disabled. The names-only mapping at `~/.codex/doppler/integrations.json` and existing private profiles are preserved. The managed wrapper uses the local service. [Toolkit guide](agent-toolkit.md)

## Restart and rotation

1. To rotate the **service token**, put the replacement in GitHub's `development` environment and rerun **Bootstrap VPS** for each affected host. If deployment stops after saving the replacement, rerun with the same token. Ansible restarts the service if it still has the old credential. Once the saved and running credentials match, another run preserves the ciphertext and service start time.
2. Check an intended secret from each host, then revoke the old token in Doppler. The playbook does not create or revoke Doppler tokens.
3. To change an **application credential**, update it in Doppler. The next wrapper invocation gets the new value. A running program keeps its existing environment.

After a reboot, systemd starts the socket and service and decrypts the stored credential locally. Fetching an application secret still needs Doppler. Planned updates reset the service start-limit counter before restarting it.

## Permissions and recovery

| Component | Where it lives and who can use it |
| --- | --- |
| Service token at rest | Ciphertext at `/etc/credstore.encrypted/doppler-secrets/doppler_token`. The host key at `/var/lib/systemd/credential.secret` is root-only. Root or anyone with both files can decrypt the token, including from a complete disk copy. |
| Runtime credential | systemd supplies the decrypted token to the non-login `doppler-secrets` service account with `LoadCredentialEncrypted`. The service keeps it out of the application environment. |
| Local requests | The root-owned `/run/doppler-secrets.sock` allows the development user's primary group to connect. The service checks the caller's UID and accepts explicit names. It fetches them over verified HTTPS with redirects and local cache fallback disabled. |
| Deployment | Only the routine host configuration receives the service token. It travels over SSH and stdin with logging and diffs suppressed. Keep SSH pipelining enabled and remote module-file retention disabled. |

The development user has sudo. Selecting names limits what a command receives, while the service token determines what that account can request. Preserve the system-wide host key for recovery. If it is lost, recover it or deliberately remove only this role's unreadable ciphertext before reprovisioning the token. Replacing the system-wide key can affect other encrypted credentials. [systemd credentials](https://www.freedesktop.org/software/systemd/man/252/systemd-creds.html) · [Doppler Service Tokens](https://docs.doppler.com/docs/service-tokens)
