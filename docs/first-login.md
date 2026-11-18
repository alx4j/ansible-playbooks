# Your first login after bootstrap

SSH in as the development user with an authorized key. Bootstrap installs the deployment public key. Your workstation needs its matching private key or another key already authorized for that account.

1. **Sign in to Codex.** Run `codex login --device-auth` and complete device authorization in your browser.
2. **Connect ClickUp.** Register the server and approve workspace access once. [Authorize ClickUp MCP](#authorize-clickup-mcp)
3. **Pair your phone** if you use Moshi. Keep the terminal open while you scan its QR code. [Pair Moshi](#pair-moshi-with-the-qr-code)
4. **Approve TokenEconomizer's hooks** if the plugin is enabled. [Review the hooks](#approve-tokeneconomizer-hooks)

## Authorize ClickUp MCP

1. If `clickup` is missing from `~/.codex/config.toml`, add the official server entry.

   ```toml
   [mcp_servers.clickup]
   url = "https://mcp.clickup.com/mcp"
   ```

2. Start OAuth login in the VPS terminal.

   ```bash
   codex mcp login clickup --no-browser
   ```

3. Open the printed URL in your local browser, select the intended ClickUp Workspace, and approve access. Paste the resulting callback URL into the waiting VPS terminal.
4. Start Codex and ask it to use ClickUp MCP to identify the signed-in user. Codex reuses the authorization until ClickUp requires a new login. This connection uses OAuth independently of any API token in Doppler.

## Pair Moshi with the QR code

1. Run `moshi-hook host setup` in your own SSH terminal and review the proposed host changes.
2. On your phone, choose **Easy Pair** in Moshi and scan the QR code. Anyone who scans the code before expiry can claim SSH access. Keep it out of logs, screenshots, and workflow output.
3. Run `moshi-hook doctor`. If it reports missing hook pairing, complete **Agent Hooks** pairing in the app as well. The saved SSH connection opens terminals. Hook pairing delivers agent events.

Easy Pair creates a private key on the phone and adds its public key to the development account's `~/.ssh/authorized_keys` alongside existing keys. The saved connection can use SSH or Mosh. Pairing survives routine bootstrap runs. [Moshi Easy Pair](https://getmoshi.app/docs/install#easy-pair-the-host) · [Host readiness](https://getmoshi.app/docs/skill#host-readiness)

## Approve TokenEconomizer hooks

Start `codex` in a project. Review TokenEconomizer when prompted, or open `/hooks`. Exit and start a new session, then enter `$tokeneconomizer:status` in the Codex prompt. [Plugin settings and usage](tokeneconomizer.md)

Once your accounts are connected, [open a project workspace](herdr.md#open-a-project-workspace).
