# Development VPS playbooks

Configure a development VPS with a dedicated account, sudo and SSH access, automatic updates, Podman, Java, SDKMAN, Gradle, Codex, Moshi, and Mosh.

The managed host needs Debian/Ubuntu, Python 3, OpenSSH, APT, and systemd. The configuration supports AMD64 and ARM64; container tests exercise AMD64 on Debian 13. GitHub Actions provides the controller, so you do not need Ansible installed on your workstation.

## Deploy through GitHub Actions

1. Follow [deployment setup](docs/github-deployment.md#1-configure-the-environment) to save the `SUDO_USER` variable, deployment private key, `SUDO_PASSWORD` secret, and trusted host keys in the `development` GitHub environment. Registered servers and their bootstrap secret names live in the [development inventory](inventories/development/hosts.yml).
2. Open **Actions → Bootstrap dev server → Run workflow**. For a registered server, enter its hostname and leave the initial credentials blank to use its saved administrator and password secret. For an unlisted server, supply its initial credentials. One run creates the configured account when needed, verifies its key and sudo access, and configures the server.
3. For later runs, enter the server address and leave the initial credentials blank. Clicking **Run workflow** applies the configuration using existing development-account access.

Deployments are manual. Pull requests and pushes to `main` run validation only. Keep provider-console access available when changing SSH or sudo.

## Local playbooks

The setup workflow runs these stages automatically. They are also available for [local runs](docs/development.md#bootstrap-and-apply).

| Playbook | Purpose |
| --- | --- |
| `bootstrap.yml` | Connect as the initial administrator; create the development account, sudo access, and approved keys. Leave SSH policy unchanged. |
| `site.yml` | Verify development-user login and sudo, then configure the host. |

## Documentation

- [GitHub deployment](docs/github-deployment.md): environment setup, credentials, and manual runs.
- [Configuration](docs/configuration.md): shared defaults, host overrides, ownership, and account onboarding.
- [Development and local runs](docs/development.md): controller setup, local commands, and verification.
- [Setup history](ansible.md): the historical commands that informed these playbooks.
