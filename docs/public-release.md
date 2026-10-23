# Share a clean fork

[Home](../README.md) · [Agent install](agent-install.md) · [Installation](portable-installation.md) · [Troubleshooting](troubleshooting.md)

The public project should be installable with the reader's own accounts and Computer. Nobody needs your private recovery history to use it.

## Export the product

Commit and review the exact source you want to share. From that checkout, export to a new directory:

```bash
npm run export:shareable -- \
  --destination /tmp/openbot-shareable \
  --repository YOUR_ACCOUNT/YOUR_REPOSITORY
```

Replace the repository placeholder with your own destination. The directory must not already exist. The export reads committed Git content, not uncommitted edits. It writes a source manifest with per-file SHA256 hashes and replaces installer/project links for the destination repository. The export requires committed public README and configuration-skill templates under docs/shareable-readme* and docs/shareable-config; missing templates refuse the export before it copies private source. CI verifies this export on every source change.

It excludes Git history, private incident evidence, fleet state, encrypted recovery archives, credentials, runtime logs/transcripts, and vendor host bundles. The recovery guide and configuration skill included in an export are product documentation, not the private incident journal. Prepared local engine generations and captured vendor bundles stay private on the Computer. The public guide explains the [coordinated Official/local update](updating.md) without another installation's receipts or node identities. The exporter also runs a fail-closed content audit: credentials, saved logins, non-synthetic identities, personal home paths, symlinks, and runtime-state artifacts refuse the export. Test identities are deterministically rewritten into a reserved synthetic namespace. Incident documentation and the installation-specific fleet instruction utility are excluded. Review remains necessary; an automated scan cannot prove every arbitrary sentence is non-personal.

Initialize the exported tree as a **new private repository** and review it there first. Making an old recovery repository public can expose deleted material through its history. This guide does not authorize changing repository visibility.

Run `node scripts/public-audit.mjs /path/to/export --deny-file /private/denied-strings.json` with an installation-specific private JSON list before publication. Keep that list outside the export. Credential and denylist checks also scan readable metadata in permitted binary images and fonts. They do not recognize faces, read text rendered inside images, or inspect compressed metadata; review assets separately or omit them. Diagnostics report filenames and rule names, never matched secrets. Start a new Git history; never copy the private `.git` directory or backups.

## Before making it public

1. **Scan contents and history.** Check source, examples, fixtures, screenshots, CI logs, artifacts, releases, and Git history for credentials and private context. If a real secret was committed, rotate it; deleting the latest copy is insufficient.
2. **Use the public repo everywhere.** README prompts, installer defaults, package metadata, docs, and skill links must point to the intended fork. Do not silently reinstall the private recovery repo.
3. **Test a fresh owned Computer.** No borrowed credentials, personal PVE service, copied fleet files, or hidden existing engine policy. Establish what the installer supplies and what needs separate compatible-engine setup.
4. **Run product checks.** Use the supported Node runtime, install dependencies from the lockfile, then run the commands below. Read skipped tests and fixture limits rather than presenting counts as real-client proof.
5. **Verify actual behavior.** Add an account, complete a new bot turn/tool result, switch the global default on an inherited bot, test an intentional pin/effort, and inspect a completed stream. Test each intended client separately.
6. **Check recovery.** Verify update preservation, deferred restart, encrypted state restoration, uninstall, and whichever no-VPN/Tailscale access paths you advertise.
7. **Keep the limits visible.** Standalone mobile admission, voice continuation, unknown upstream ports, provider limits, and native backup status need their actual evidence. Do not replace this with “everything works.”

```bash
npm ci
npm test
npm run typecheck
npm run build
python3 -m unittest discover -s recovery/hooks -p 'test_*.py'
```

Offline/mock tests do not prove a fresh native deployment. Record the tested host/client versions and distinguish live provider, synthetic transport, and physical-client tests.

## Publish only the product

Include a license, installer and uninstall path, prerequisites, paste-ready prompts, provider/account setup, compatibility notes, troubleshooting, and source/version provenance. Keep runtime archives, bot histories, prepared vendor bundles, login files, personal addresses, and tailnet topology out of public releases.

A release should identify the exact commit it installs and the tests that passed. Let people use their own accounts with or without Tailscale. Explain optional components instead of assuming another installation's services exist.

## Contributions

Start from the latest main branch in a repository-local worktree. Read [AGENTS.md](../AGENTS.md) before changing code. Use a focused pull request with the concrete behavior, relevant tests, and any native/client verification limits. Provider additions need their direct origin, supported protocol, credential method, scoped model metadata, and account isolation.

Never include keys in fixtures or bindings. Never “fix” a test by removing bot settings, changing the selected account, clearing ownership holds, or claiming that initial HTTP 200 was a delivered reply.
