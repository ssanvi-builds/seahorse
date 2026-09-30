"""The parity contract over HTTP — the same 9-step session stdio drives.

``run_contract_session`` rides the in-process Streamable HTTP server,
asserting byte-for-byte the protocol behavior the stdio smoke proves through
real pipes. The two DIVERGENT behaviors are proven in the envelope file and
are NOT duplicated here:

- notification → ``202`` + ``Content-Length: "0"`` (stdio: no stdout line) —
  ``test_http_server.py::test_notification_returns_202_empty_body``
- malformed body → ``400`` + ``-32700`` WITHOUT ``id`` (stdio: ``id: null``) —
  ``test_http_server.py::test_malformed_body_returns_parse_error_without_id``
"""

from __future__ import annotations

import json

# Fixture helpers ride the envelope file: pytest resolves the "http_server"
# fixture from this module's namespace — the cross-module import IS the wiring.
from tests.mcp.contract_session import run_contract_session
from tests.mcp.test_http_server import _json, _post, http_server  # noqa: F401


def test_full_session_over_http_matches_stdio(http_server) -> None:  # noqa: F811
    """The 9-step parity session rides HTTP 200 on every id'd request."""
    _, url = http_server
    pending: list[dict] = []

    def send(request: dict) -> None:
        code, _headers, body = _post(url, body=_json(request))
        assert code == 200, f"transport rejected a request ({code}): {body!r}"
        pending.append(json.loads(body))

    ep_id, new_id = run_contract_session(send, lambda: pending.pop(0))
    assert ep_id
    assert new_id != ep_id