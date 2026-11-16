# Development VPS playbooks

Configure a development VPS with a `dev` account, sudo and SSH access, automatic updates, Podman, Java, SDKMAN, Gradle, Codex, Moshi, and Mosh.

The managed host needs Debian/Ubuntu, Python 3, OpenSSH, APT, and systemd. The configuration supports AMD64 and ARM64; container tests exercise AMD64 on Debian 13. GitHub Actions provides the controller, so you do not need Ansible installed on your workstation.

## Deploy through GitHub Actions

1. Merge the workflows into the default branch.
2. Follow [deployment setup](docs/github-deployment.md#1-configure-the-environment) to save the deployment private key, `DEV_PASSWORD`, and trusted host keys in the `development` GitHub environment. No inventory YAML is needed in GitHub.
3. Open **Actions → Bootstrap dev server → Run workflow**. For a new host, enter the server address, initial administrator username, and password, then select `apply`. One run creates `dev`, verifies its key and sudo access, and configures the server. The initial password is an ordinary workflow input with log masking.
4. For later runs, enter the server address and leave the initial credentials blank. The workflow reuses working `dev` access. Use `check` to preview changes or `apply` to make them; see [check-mode limitations](docs/github-deployment.md#3-check-review-and-apply).

Both server workflows use the `development` environment. Use **Maintain dev server** separately to upgrade installed packages.

Deployments are manual. Pushes and pull requests run validation only. Keep provider-console access available when changing SSH or sudo.

## Local playbooks

The setup workflow runs these stages automatically. They are also available for [local runs](docs/development.md#bootstrap-and-apply).

| Playbook | Purpose |
| --- | --- |
| `bootstrap.yml` | Connect as the initial administrator; create the development account, sudo access, and approved keys. Leave SSH policy unchanged. |
| `site.yml` | Verify development-user login and sudo, then configure the host. |
| `maintenance.yml` | Upgrade packages; optionally purge unused dependencies and reboot when required. |

## Documentation

- [GitHub deployment](docs/github-deployment.md): environment setup, credentials, and manual runs.
- [Configuration](docs/configuration.md): shared defaults, host overrides, ownership, and account onboarding.
- [Development and local runs](docs/development.md): controller setup, local commands, and verification.
- [Setup history](ansible.md): the historical commands that informed these playbooks.
