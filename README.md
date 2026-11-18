# Development VPS bootstrap

Keep your development environment on a VPS and pick up the same terminal from your laptop or phone. Run one GitHub Actions workflow to set up the server. Open Herdr, pick a project, and start coding. Your shell, build, or Codex session can keep running when you disconnect.

## What you get

| When you want to… | What bootstrap sets up |
| --- | --- |
| Start work on a project | Git clones the GitHub repositories you tag into a private projects folder. Herdr gives each managed checkout a terminal workspace. Existing branches and edits stay where you left them. |
| Build and test | Zulu Java 25 and Gradle through SDKMAN, per-project Java selection through jenv, Go, Rust and Cargo, plus Podman and Podman Compose for containers. |
| Work with Codex | The Codex CLI, a toolkit of skills and global instructions, and TokenEconomizer for per-project tool and usage settings. You authorize your own Codex and ClickUp accounts. |
| Switch devices | Herdr keeps terminals alive across SSH disconnects. Moshi on your phone can find project workspaces and show agent events. Mosh supports roaming connections. |
| Use credentials in a command | A local Doppler service passes selected secrets to the program that needs them. It works in SSH, tmux, and Herdr. |
| Keep the host maintained | A development account with sudo and key-based SSH, daily unattended updates, and verified SSH settings. Automatic reboots and firewall activation are opt-in. |

## Set up your first server

1. **Bring a reachable VPS.** Fork this repository or use a copy you administer. Use Debian or Ubuntu with Python 3, OpenSSH, APT, and systemd. The roles support AMD64 and ARM64. CI exercises Debian 13 and Ubuntu 24.04 on AMD64. Keep access to the provider console while setting up SSH.
2. **Choose your projects and integrations.** The defaults sync repositories owned by `alx4j` and install that owner's Codex toolkit. For your own VPS, choose your GitHub owner and topic or turn off these integrations. Both enabled integrations need a GitHub credential in Doppler. [Setup choices](docs/github-deployment.md#choose-what-to-install)
3. **Run Bootstrap VPS.** Save the development account, deployment key, sudo password, and trusted host keys in GitHub. Then open **Actions → Bootstrap VPS → Run workflow**. GitHub runs Ansible for you. [GitHub walkthrough](docs/github-deployment.md) · [Local alternative](docs/development.md#prepare-the-controller)
4. **Finish the account setup over SSH.** Sign in to Codex, authorize ClickUp in your browser, pair Moshi if you use a phone, and review TokenEconomizer's hooks. [First-login checklist](docs/first-login.md)

## Open your first workspace

In the development account's SSH shell, run:

```bash
cd "$PROJECTS_DIR"
herdr
```

Pick a project in the sidebar and run `codex` or your usual build command. Press **Ctrl-B, Q** to detach. Reconnect with `herdr` or select the same workspace in Moshi. With project sync disabled, you can create a workspace for any checkout yourself. [Herdr and Moshi guide](docs/herdr.md)

## Make it yours

| I want to… | Guide |
| --- | --- |
| Choose a Java version, projects folder, SSH port, or firewall policy | [Host configuration](docs/configuration.md#common-host-overrides) |
| Add projects, find an archived checkout, or change the GitHub owner | [Work repositories](docs/work-repositories.md) |
| Use a secret from a terminal or rotate the server's access | [Doppler](docs/doppler.md) |
| Change the skills Codex installs or edit global instructions | [Codex toolkit](docs/agent-toolkit.md) |
| Adjust tool pruning or repeated-failure prompts | [TokenEconomizer](docs/tokeneconomizer.md) |
| Update servers, run the tests, or change a tool version | [Deployment](docs/github-deployment.md#3-apply-later-changes) · [Verification](docs/development.md#verification) · [Updates](docs/updates.md) |
| See how the original host was set up by hand | [Setup history](docs/history/original-host.md) |
