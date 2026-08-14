"""``frontmatter.handler`` — ruamel round-trip handler."""

from __future__ import annotations

from ruamel.yaml.comments import CommentedMap

from seahorse.frontmatter.handler import RuamelRTHandler, _make_yaml


def test_make_yaml_is_round_trip() -> None:
    y = _make_yaml()
    # typ='rt' is what preserves comments/quotes/order; the others are canonical-format settings.
    assert y.typ == ["rt"]
    assert y.preserve_quotes is True
    assert y.width == 4096


def test_handler_load_returns_commented_map_preserving_order() -> None:
    yaml = _make_yaml()
    handler = RuamelRTHandler(yaml)
    fm = "b: 1\na: 2\n"
    cm = handler.load(fm)
    assert isinstance(cm, CommentedMap)
    # ruamel rt preserves declaration order (no alphabetical sort).
    assert list(cm.keys()) == ["b", "a"]


def test_handler_load_empty_frontmatter_is_empty_map() -> None:
    handler = RuamelRTHandler(_make_yaml())
    assert isinstance(handler.load(""), CommentedMap)
    assert len(handler.load("")) == 0
    assert len(handler.load("   \n")) == 0


def test_handler_load_non_mapping_is_empty_map() -> None:
    # A bare scalar where a mapping is expected: normalize to empty so the
    # caller raises a typed validation error rather than a ruamel TypeError.
    handler = RuamelRTHandler(_make_yaml())
    cm = handler.load("just a scalar")
    assert isinstance(cm, CommentedMap)
    assert len(cm) == 0


def test_handler_export_round_trips_a_commented_map() -> None:
    yaml = _make_yaml()
    handler = RuamelRTHandler(yaml)
    cm = CommentedMap()
    cm["a"] = 1
    cm["b"] = "two"
    text = handler.export(cm)
    assert "a: 1" in text
    assert "b: two" in text


def test_handler_detects_frontmatter_boundary() -> None:
    handler = RuamelRTHandler(_make_yaml())
    assert handler.detect("---\ntitle: foo\n---\nbody") is True
    assert handler.detect("no frontmatter here") is False


def test_handler_split_returns_body_byte_a_byte() -> None:
    # The closing delimiter '\n---\n' consumes exactly ONE separator newline,
    # so a body with no leading newline ('# Body\n') parses back to itself —
    # byte-a-byte, no spurious leading '\n'. (BaseHandler's greedy '\s*' would
    # leak a '\n' here; this override keys on the canonical write format.)
    handler = RuamelRTHandler(_make_yaml())
    fm, body = handler.split("---\ntitle: foo\n---\n# Body\n\n")
    assert body == "# Body\n\n"
    assert "# Body" in body


def test_handler_split_preserves_leading_blank_line_in_body() -> None:
    # A body whose own first line is blank ('' then '# Body') keeps it: writing
    # '---\n{fm}\n---\n\n# Body\n' parses back to '\n# Body\n' (the body's
    # leading blank line survives; only the one separator newline is consumed).
    handler = RuamelRTHandler(_make_yaml())
    _fm, body = handler.split("---\ntitle: foo\n---\n\n# Body\n")
    assert body == "\n# Body\n"


def test_handler_split_no_body_returns_empty() -> None:
    # '---\n{fm}\n---\n' (closing delimiter as last line, no body) → empty body.
    handler = RuamelRTHandler(_make_yaml())
    _fm, body = handler.split("---\ntitle: foo\n---\n")
    assert body == ""


def test_handler_split_closing_delimiter_at_eof_no_trailing_newline() -> None:
    # The _FM_CLOSE_END branch: '---\n{fm}\n---' with NO trailing newline (the
    # closing '---' is the very last bytes of the file, no '\n' after). Must
    # still parse as frontmatter with an empty body, not fall through to "no fm".
    handler = RuamelRTHandler(_make_yaml())
    fm, body = handler.split("---\ntitle: foo\n---")
    assert fm == "title: foo"
    assert body == ""


def test_handler_split_no_frontmatter_returns_empty_fm_and_whole_text() -> None:
    handler = RuamelRTHandler(_make_yaml())
    fm, body = handler.split("no frontmatter here\n# Heading\n")
    assert fm == ""
    assert body == "no frontmatter here\n# Heading\n"