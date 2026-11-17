# Herdr and Moshi

Ansible installs Herdr TUI for the development account alongside Moshi and its
Codex hooks. Herdr keeps terminals running across SSH disconnects; Moshi lets
you attach to those workspaces from your phone and receive agent events.

## Start a workspace

After running **Bootstrap VPS**, connect over SSH as the configured development
account and run:

```bash
cd "$PROJECTS_DIR"
herdr
```

This starts or attaches to the default session. Inside Herdr, create a workspace
for an existing project directory:

```bash
herdr workspace create --cwd "$PROJECTS_DIR/my-project" --label my-project --focus
```

Run `codex` in that workspace. Press **Ctrl-B, Q** to detach while its processes
keep running. Reconnect in Moshi and select the workspace from the Herdr tab.
An empty picker usually means no Herdr server is running yet.

Ansible installs the binary without starting sessions, attaching automatically,
or changing your Herdr configuration. A repeat run preserves running sessions.
Replacing the binary during an upgrade does not restart an existing Herdr server;
finish its work before stopping it with `herdr session stop default` and starting
it again. Disconnect survival does not mean processes survive a server reboot.

## Pair the phone once

If this host is already paired with Moshi, reconnect and choose Herdr. There is
no separate Herdr pairing token.

For a new host, run `moshi-hook host setup` yourself in an interactive terminal
and follow its QR pairing flow in the Moshi app. Review its proposed host
changes before accepting them. Ansible already manages SSH, Mosh, Codex hooks,
and the `moshi-hook.service` user service. Pairing credentials stay outside the
repository and are not requested by the workflow.

The Moshi daemon recognizes Herdr's session environment and includes workspace
context in supported agent events. Start Codex inside Herdr so that context is
available. The existing Doppler wrapper works in these shells too.

## Check discovery

From another machine, use your saved SSH connection:

```bash
ssh your-server 'command -v herdr; herdr --version'
ssh your-server 'moshi-hook mux ls --json'
```

Ansible places `~/.local/bin` on PATH at the start of `.bashrc`, before the
distribution's noninteractive return. Moshi's systemd service already includes
this directory in its own PATH. No additional listening port or firewall rule
is needed for Herdr's local sessions.

`moshi <directory>` remains a tmux launcher. Use `herdr workspace create` for
Herdr projects. tmux remains installed for existing sessions and that launcher.

The guide's window-title plugin is optional and needs Bun; neither is required
for session discovery or agent events, so this setup does not install them.

## Versions

`dev_host_herdr_version` and `dev_host_herdr_checksums` in the role defaults pin
the Linux AMD64 and ARM64 binaries. Update them together using the official
release manifest. Avoid `herdr update` on an Ansible-managed host: the next
playbook run restores the pinned binary.

Ansible disables Moshi's automatic updater to keep its pinned version stable.
Change `dev_host_moshi_version` through the normal PR and deployment workflow.
Other Moshi settings and pairing are preserved.

References: [Moshi with Herdr](https://getmoshi.app/guides/herdr),
[Moshi's Herdr reference](https://getmoshi.app/docs/herdr),
[Herdr installation](https://herdr.dev/tui/docs/install/), and
[release manifest](https://herdr.dev/latest.json).
