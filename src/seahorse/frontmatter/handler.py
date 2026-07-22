"""ruamel.yaml round-trip handler for python-frontmatter (f5-03 §4.1/§7.1).

F3.1 sec 4.2 mandates a single YAML engine that preserves comments, key order,
quote style, block scalars, and anchors/aliases. ``python-frontmatter`` defaults
to PyYAML (safe load/dump), which destroys all of those. F3.3 cables a custom
``RuamelRTHandler`` that delegates parse/dump to ``ruamel.yaml`` ``typ='rt'``
(round-trip), so ``parse_file`` receives a ``CommentedMap`` carrying the
file's original formatting — the baseline ``_merge_known`` writes back onto, so
``x-*`` keys, comments, and inherited quote styles survive the round-trip.

This handler is confined to ``seahorse/frontmatter/`` (it imports
``ruamel.yaml`` and ``frontmatter``); neither leaks into the core.
"""

from __future__ import annotations

import io
import re
from typing import Any

from frontmatter.default_handlers import BaseHandler
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap


def _make_yaml() -> YAML:
    """A round-trip ruamel YAML tuned for frontmatter preservation (f5-03 §4.1).

    - ``typ='rt'``: round-trip mode (keeps comments / quotes / order / anchors).
    - ``preserve_quotes=True``: keep the original quote style on scalars.
    - ``width=4096``: do not wrap long scalar lines (frontmatter values stay
      one-line; wrapping would mutate the file on every write).
    - ``indent(mapping=2, sequence=4, offset=2)``: the canonical F3.1 indent.
    - explicit ``null``: ruamel emits ``None`` as an empty value by default
      (``key:``); F3.1 MVP-1 wants the canonical ``key: null`` literal (f5-03
      §4.4). A registered representer forces the ``null`` scalar so MVP-1
      ``exclude_none=False`` writes explicit nulls. MVP-0 uses
      ``exclude_none=True`` so no ``None`` reaches the dumper — the representer
      is inert there.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.width = 4096
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.Representer.add_representer(type(None), _represent_none_explicit)
    return yaml


def _represent_none_explicit(representer: object, data: object) -> object:
    """Emit ``None`` as the explicit scalar ``null`` (ruamel round-trip)."""
    return representer.represent_scalar(  # type: ignore[attr-defined]
        "tag:yaml.org,2002:null", "null"
    )


class RuamelRTHandler(BaseHandler):
    """A python-frontmatter handler backed by ruamel.yaml round-trip.

    ``load`` returns a ``CommentedMap`` (not a plain dict) so the caller can
    mutate known fields while preserving comments, ``x-*`` keys, and order.
    ``export`` mirrors ``load`` for completeness (used only if someone calls
    ``frontmatter.dumps``; the adapter writes via ``_dump_yaml`` directly).
    """

    FM_BOUNDARY = re.compile(r"^-{3,}\s*$", re.MULTILINE)
    START_DELIMITER = END_DELIMITER = "---"
    # Opening delimiter: a '---' line at the very start of the file. We accept
    # trailing horizontal whitespace (Obsidian sometimes writes '---   ').
    _FM_OPEN = re.compile(r"^-{3,}[ \t]*\n")
    # Closing delimiter: a '---' line preceded and followed by '\n'. Matching
    # '\n---\n' (not '^-{3,}$') is what makes the body byte-a-byte reversible: it
    # consumes exactly ONE separator newline, so a body '# Madrid\n' written as
    # '...---\n# Madrid\n' parses back to '# Madrid\n' (no spurious leading '\n').
    _FM_CLOSE = re.compile(r"\n-{3,}[ \t]*\n")
    _FM_CLOSE_END = re.compile(r"\n-{3,}[ \t]*$")

    def __init__(self, yaml: YAML, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.yaml = yaml

    def split(self, text: str) -> tuple[str, str]:
        """Split frontmatter from body, returning ``(fm, body)`` byte-a-byte.

        Overrides ``BaseHandler.split`` (which uses ``^-{3,}\\s*$`` and a
        greedy ``\\s*`` that consumes a variable number of trailing newlines,
        making round-trips non-reversible — a body ``# Madrid`` gains a
        leading ``\\n`` on each parse). This implementation keys on the
        canonical write format ``---\\n{fm}\\n---\\n{body}``:

        - opening ``---\\n`` at the start of the file;
        - closing ``\\n---\\n`` (or ``\\n---`` at end-of-file with no body);
        - ``body`` is whatever follows the closing delimiter, byte-a-byte.

        A body that itself starts with a blank line keeps it: writing
        ``---\\n{fm}\\n---\\n\\n# Madrid\\n`` parses back to ``\\n# Madrid\\n``.
        A body with no leading newline (``# Madrid\\n``) parses back to
        ``# Madrid\\n``. No file without an opening ``---`` line is treated as
        frontmatter (returns ``("", text)``).
        """
        if not self._FM_OPEN.match(text):
            return "", text
        after_open = self._FM_OPEN.split(text, maxsplit=1)[1]
        m = self._FM_CLOSE.search(after_open)
        if m is not None:
            return after_open[: m.start()], after_open[m.end() :]
        m_end = self._FM_CLOSE_END.search(after_open)
        if m_end is not None:
            # closing '---' is the last line, no body follows
            return after_open[: m_end.start()], ""
        # malformed (opening '---' but no closing): treat the rest as fm, no body
        return after_open, ""

    def load(self, fm: str, **kwargs: Any) -> CommentedMap:
        # An empty frontmatter block (case A: no metadata) parses to an empty
        # map, not None.
        if not fm.strip():
            return CommentedMap()
        data = self.yaml.load(fm)
        if data is None:
            return CommentedMap()
        if not isinstance(data, CommentedMap):
            # A non-mapping frontmatter (e.g. a bare scalar) is invalid F3.1;
            # normalize to an empty map so model_validate raises a typed error
            # rather than a ruamel TypeError.
            return CommentedMap()
        return data

    def export(self, metadata: Any, **kwargs: Any) -> str:
        buf = io.StringIO()
        self.yaml.dump(metadata, buf)
        return buf.getvalue().strip()