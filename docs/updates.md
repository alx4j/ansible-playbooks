# Dependency and tool updates

## Dependabot

The [Dependabot configuration](../.github/dependabot.yml) checks GitHub Actions and Python requirements every Monday at 08:00 UTC after it reaches `main`. It opens ordinary PRs; it does not merge them. No Doppler token or deployment credentials are needed for these public dependencies.

Patch and minor updates are grouped by ecosystem. Major updates stay in separate PRs. At most three version-update PRs per ecosystem remain open at a time. Review the release notes and wait for `validate` before merging. Ansible minor releases can change behavior, so read their porting notes even when an update is grouped.

Enable Dependabot alerts and security updates in the repository's code-security settings if they are not already enabled. Security-update PRs are separate from the weekly version-update schedule. Keep merges manual initially; this repository applies configuration to accessible inventory servers after a successful validation of a `main` push.

Dependabot maintains the Python pins in `requirements.txt` and `requirements-dev.txt`, and action versions in workflows. The test image reads the same Ansible core requirement as the controller, avoiding a second pin to update. Ansible collections and application versions/checksums in role defaults still need a reviewed manual update.

References: [Dependabot options](https://docs.github.com/en/code-security/reference/supply-chain-security/dependabot-options-reference), [security updates](https://docs.github.com/en/code-security/dependabot/dependabot-security-updates/configuring-dependabot-security-updates), and [Ansible porting guides](https://docs.ansible.com/projects/ansible/latest/porting_guides/porting_guides.html).

## Change the right pins together

| Component | Files and values to update |
| --- | --- |
| Ansible core | `requirements.txt`; controllers and test images share it. |
| Ansible lint | `requirements-dev.txt`; keep it compatible with core and Python 3.13. |
| Ansible collections | `requirements.yml`, including compatible transitive dependencies. |
| Go | `dev_host_go_version` and both official Linux archive SHA-256 values in `dev_host_go_checksums`. |
| Rust | `dev_host_rust_version`; this selects the compiler, standard library, and Cargo. |
| Rustup | `dev_host_rustup_version` and both architecture-specific installer checksums. |
| Java | The exact `dev_host_java_version` SDKMAN identifier; change the jenv alias when changing Java majors. |
| jenv | `dev_host_jenv_revision`, using a reviewed release commit. |
| Gradle, Codex | Their `dev_host_*_version` values; check CLI compatibility and installer checksums where applicable. |
| Moshi | No version pin. Fresh installs use the current release with checksum verification; existing installations keep their updater and settings. |
| Herdr | `dev_host_herdr_version` and both Linux SHA-256 values in `dev_host_herdr_checksums`, from the [official release manifest](https://herdr.dev/latest.json). |
| Herdr window titles | `dev_host_herdr_title_sync_revision`, using a reviewed commit from `rjyo/herdr-window-title-sync`. |
| Bun | `dev_host_bun_version` and both Linux archive SHA-256 values in `dev_host_bun_checksums`, from the release's `SHASUMS256.txt`. |
| SDKMAN/Codex installer | Review the downloaded script before updating its SHA-256 pin. |

Use [Go's download metadata](https://go.dev/dl/?mode=json), [Rust's stable manifest](https://static.rust-lang.org/dist/channel-rust-stable.toml), [rustup releases](https://github.com/rust-lang/rustup/releases), and `sdk list java` for the relevant upstream versions. An installer checksum is evidence of a reviewed artifact; do not simply replace a failed checksum with whatever the download returned.

## Verify and roll out

1. Change the pins in a branch, keeping version and checksum edits together.
2. Run `scripts/setup` and `scripts/check`. For tool changes, run `scripts/test-tools-container` to verify real installation, builds, PATH, recovery from a partial Go install, and an unchanged second run.
3. Open a PR. CI also bootstraps a fresh Ubuntu VM, executes the complete `site.yml`, checks SSH/firewall/systemd state, and requires zero changes on a second run. Service reload and restart timestamps must remain unchanged.
4. Review and merge after `validate` passes. The successful `main` run updates servers with working development-account access. Use **Bootstrap VPS** manually for an unbootstrapped server or a single-server rerun.

Go and SDKMAN upgrades retain older version directories. Changing the projects-directory name preserves the old directory and its contents. Application settings, existing passwords, unrelated SSH keys, and per-project version files retain their documented ownership rules.
