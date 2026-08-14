# Security Policy

## Reporting a vulnerability

Please **do not** report security vulnerabilities through public GitHub issues.

To report a vulnerability privately, open a [GitHub Security Advisory]
(https://github.com/ssanvi-builds/seahorse/security/advisories/new) or email the
maintainers through the contact address listed on the repository profile.

Please include, when possible:

- The Seahorse version(s) affected.
- A description of the vulnerability and its impact.
- Steps to reproduce, or a minimal proof of concept.
- Whether the issue has been discussed publicly or is already known.

You will receive a response acknowledging the report. We aim to confirm and
triage reports within a few business days. Details are kept confidential until a
fix is released.

## Scope

The core engine and CLI operate on user-supplied vaults (local markdown
directories) and are not exposed to untrusted network input in their default
configuration. Relevant areas for security review include:

- Markdown/frontmatter parsing of vault notes (malformed or hostile files).
- The MCP stdio server (agent-facing) and its wire-level input validation.
- Any optional remote-provider paths (LLM calls) — these only transmit the data
  you ask them to extract.

## Supported versions

Security fixes are applied to the latest release. If you rely on an older
release, please upgrade.
