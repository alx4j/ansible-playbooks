# Update tools and roll changes out

Open a PR with the new version and its verification data together. The same pins are used on new and existing hosts. A successful validation of a merged change can update every reachable inventory server.

## Dependabot

Dependabot checks GitHub Actions and Python requirements on Mondays at 08:00 UTC. It groups patch and minor releases by ecosystem, opens separate PRs for major releases, and keeps up to three version-update PRs open per ecosystem. It needs no deployment credentials for these dependencies.

Review release notes before merging. Ansible minor releases can change playbook behavior, so check their porting notes too. Enable alerts and security updates in the repository settings if you want security PRs outside the weekly schedule. Ansible collections and host tools use the manual process below. [Schedule and groups](../.github/dependabot.yml) · [Ansible porting guides](https://docs.ansible.com/projects/ansible/latest/porting_guides/porting_guides.html)

## Change the right pins together

| Component | What to edit |
| --- | --- |
| Controller and tests | `requirements.txt` for Ansible core, `requirements-dev.txt` for lint, `requirements.yml` for collections including transitive dependencies. Keep Python 3.13 compatibility. The controller and container share the core pin. |
| Java, jenv, Gradle | Use an exact SDKMAN identifier for `dev_host_java_version`. Adjust `dev_host_jenv_java_alias` for a new Java major. Pin jenv to a reviewed commit in `dev_host_jenv_revision` and set `dev_host_gradle_version` for Gradle. |
| Go | Update `dev_host_go_version` and both official Linux archive SHA-256 values in `dev_host_go_checksums`. |
| Rust and rustup | `dev_host_rust_version` selects the compiler, standard library, and Cargo. A rustup upgrade changes `dev_host_rustup_version` and both installer checksums. |
| Codex and installers | Update `dev_host_codex_version` for the CLI. Review changed SDKMAN or Codex installer scripts before updating their SHA-256 pins. |
| Herdr and Bun | Update `dev_host_herdr_version` and `dev_host_herdr_checksums` from Herdr's release manifest. For Bun, update `dev_host_bun_version` and `dev_host_bun_checksums` from the release's `SHASUMS256.txt`. Cover both Linux architectures. |
| Herdr window titles | Set `dev_host_herdr_title_sync_revision` to a reviewed commit from `rjyo/herdr-window-title-sync`. |
| TokenEconomizer | Update `tokeneconomizer_revision` and verify the packaged plugin and hooks with the pinned Codex CLI. |
| Codex toolkit | The managed checkout follows the toolkit's `main` on bootstrap. Ship new plugin versions through the toolkit repository. [Toolkit update steps](agent-toolkit.md#3-publish-a-toolkit-change) |
| Moshi | Fresh installs fetch the current release and verify its checksum manifest. Existing installs keep their automatic updater and settings. Check hook compatibility when changing related tools. |

Find the exact pins in the [host defaults](../roles/dev_host/defaults/main.yml) and [TokenEconomizer defaults](../roles/tokeneconomizer/defaults/main.yml). Use `sdk list java`, [Go download metadata](https://go.dev/dl/?mode=json), the [Rust stable manifest](https://static.rust-lang.org/dist/channel-rust-stable.toml), [rustup releases](https://github.com/rust-lang/rustup/releases), and the [Herdr release manifest](https://herdr.dev/latest.json) to identify the artifacts. Investigate a checksum mismatch before accepting a new value.

## Verify and roll out

1. Edit the pins on a branch, then run `scripts/setup` and `scripts/check`.
2. For a host tool change, run `scripts/test-tools-container`. It checks real installs, command lookup, builds, repair of an incomplete Go installation, and the second playbook run. [Verification guide](development.md#verification)
3. Open a PR and review the results, including the hosted VM run. The VM applies the configuration twice and checks that services stay running on the second pass.
4. Merge when `validate` passes. Watch **Bootstrap VPS** after validation on `main` and read the status of each server. Use a manual run for first setup or a single-server rerun. [Read the job summary](github-deployment.md#3-apply-later-changes)

Go and SDKMAN upgrades keep older installation directories. Existing project version files and unrelated SSH keys retain their ownership. To switch the running Herdr server to its new binary, finish any active work before restarting the session. [Herdr upgrades](herdr.md#versions)
