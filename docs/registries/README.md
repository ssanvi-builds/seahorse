# Registry publishing

The repo ships the manifests for the three directories that matter in
practice. The files live here; the actual submissions are manual (they
publish to external services — do them at release time, after the tag).

## What ships in the repo

| File | Registry | Status |
|------|----------|--------|
| `server.json` | Official MCP registry (preview) | ready to publish |
| `smithery.yaml` | Smithery | ready to publish |
| `docs/registries/mcpm.json` | mcpm.sh community registry | ready to submit |

## 1. Official MCP registry (`server.json`)

1. **Bump the version** — `server.json` carries the version twice (top-level
   and in the `packages[]` entry). Bump it in the same commit as the
   `pyproject.toml` bump; versions are immutable once published.
2. **Schema gotchas** (validated against `2025-12-11`): `description` is
   capped at 100 chars; `repository` needs `source` (`"github"`), `id` (the
   GitHub repo ID **as a string** — `gh api repos/<owner>/<repo> --jq '.id'`)
   and `url`. Re-validate before publishing:
   ```bash
   curl -sL https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json -o /tmp/server.schema.json
   uvx --with jsonschema --from python python -c "import json,jsonschema; jsonschema.validate(json.load(open('server.json')), json.load(open('/tmp/server.schema.json'))); print('VALID')"
   ```
2. **Ownership verification** (PyPI package type): the README must contain the
   literal string `mcp-name: io.github.ssanvi-builds/seahorse-memory` — it is
   already there; keep it on any README restructure.
3. Publish:
   ```bash
   curl -X POST https://registry.modelcontextprotocol.io/v0.1/publish \
     -H "Content-Type: application/json" \
     --data @server.json
   ```
   Validate without publishing first via `POST /v0.1/validate`. The namespace
   `io.github.ssanvi-builds/...` requires GitHub OIDC login at publish time.
4. Listing URL to verify: `https://registry.modelcontextprotocol.io/v0.1/servers/io.github.ssanvi-builds%2Fseahorse-memory`.

## 2. Smithery (`smithery.yaml`)

```bash
npx -y @smithery/cli publish
```

The server is stdio with a single optional config field (`vault`); the
`commandFunction` in `smithery.yaml` turns it into the `uvx` invocation.

## 3. mcpm.sh (`docs/registries/mcpm.json`)

Two accepted routes (their `mcp-registry/README.md`):

- **Issue (simplest)**: open an issue in `pathintegral-institute/mcpm.sh`
  titled `Add server: seahorse-memory` linking the repo — maintainers
  generate the entry.
- **PR (direct)**: fork, copy this JSON to
  `mcp-registry/servers/seahorse-memory.json`, run their
  `python scripts/validate_manifest.py | grep seahorse-memory`, submit.

## 4. `uvx` smoke test (do this before any submission)

```bash
uvx --from seahorse-memory seahorse-mcp --help
# and a full roundtrip through the profile:
printf '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"probe","version":"0"}}}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n' \
  | uvx --from seahorse-memory seahorse-mcp
```

If the PyPI release is fresh, confirm the published wheel carries the same
tool count (15) before the listings go live — a registry entry that advertises
stale tools is worse than a later listing.