# Keep a project terminal running

Start a build or a Codex session in Herdr on your laptop, detach, and open the same workspace on your phone with Moshi. Herdr runs the terminal on the VPS. SSH or Mosh connects your device to it.

## Open a project workspace

1. After bootstrap, SSH in as the development user and start Herdr.

   ```bash
   cd "$PROJECTS_DIR"
   herdr
   ```

2. Select a project in the sidebar. Repository sync creates workspaces for selected projects in the `default` session. New workspaces open a shell in the checkout. Run `codex`, your tests, or your build there.
3. Press **Ctrl-B, Q** to detach. The shell and its processes keep running on the host. Reconnect with `herdr` or choose the workspace in Moshi's **Herdr** tab.

For a manually cloned project, create its workspace from a shell on the host. Replace `my-project` with the directory name of your checkout.

```bash
herdr workspace create --cwd "$PROJECTS_DIR/my-project" --label my-project --focus
```

Set `work_repositories_herdr_session` to choose a named session for managed projects. Connect with `herdr --session <name>`. Sync reuses existing workspaces and preserves custom names and layouts. After a host reboot, open Herdr or rerun sync to restore the saved layout. Processes that were running before the reboot have ended. [Project synchronization](work-repositories.md#project-workspaces)

## Pair the phone once

From an existing SSH connection, run `moshi-hook host setup` and scan the QR code with Moshi's **Easy Pair**. A paired phone can use the Herdr tab immediately. Start Codex **inside** a Herdr workspace so supported Moshi events can include its context. Ansible installs the Moshi user service, Codex hooks, and terminal integration. [Pairing and verification](first-login.md#pair-moshi-with-the-qr-code)

## Find a missing session

If the phone's picker is empty, first run `herdr` on the host. Installation alone starts no project session. Sync starts one when it has managed projects. From a second machine, substitute your saved SSH hostname and check what Moshi can discover.

```bash
ssh your-server 'herdr --version'
ssh your-server 'moshi-hook mux ls --json'
```

Both noninteractive SSH and the Moshi user service include `~/.local/bin` on PATH. Herdr sessions use a local connection on the host. The separate `moshi <directory>` command launches tmux. To open a Herdr project, use the Herdr command above.

## Window titles

The bundled title plugin updates the outer terminal title when you change workspace, tab, or agent. Moshi uses it as the session label. Its fallback can include text from a recent Codex or Claude prompt, which can therefore appear on your phone.

```bash
bun --version
herdr plugin list
herdr plugin action invoke rjyo.window-title-sync.refresh
```

Refresh requires a running session. For a named session, add `--session <name>` immediately after `herdr`. [Window title plugin](https://github.com/rjyo/herdr-window-title-sync)

## Versions

Ansible manages the Herdr binary, Bun, and the title plugin's linked checkout at `~/.local/share/herdr-window-title-sync`. Change their pins in the role defaults. The playbook restores the selected versions and enabled state on later runs. It preserves other plugins, configuration, and live sessions. After upgrading Herdr, finish your running work before using `herdr session stop default` and starting it again to load the new binary.

Moshi installs the current checksum-verified release on fresh hosts and keeps its updater, settings, and existing pairing. [Tool update guide](updates.md) · [Herdr in Moshi](https://getmoshi.app/docs/herdr) · [Herdr releases](https://herdr.dev/latest.json)
