"""Validate canonical_body_hash — the body-only digest used by the engine.

Body-only SHA-256 hex 64. Normalization = NFC + rstrip per line + collapse 3+
blank lines to 2 + strip trailing newlines. Frontmatter is EXCLUDED (the caller
passes body only) so re-import is idempotent across runs where engine-owned
timestamps differ.
"""

from __future__ import annotations

import unicodedata

from seahorse.engine.canonical import canonical_body_hash


def test_canonical_hash_is_sha256_hex_64():
    h = canonical_body_hash("# Title\nbody")
    assert len(h) == 64
    int(h, 16)  # parseable hex


def test_same_body_same_hash():
    assert canonical_body_hash("hello\nworld") == canonical_body_hash("hello\nworld")


def test_different_body_different_hash():
    assert canonical_body_hash("hello\nworld") != canonical_body_hash("hello\nworld2")


def test_nfc_normalization_nfd_equivalent():
    # é as precomposed (NFC) vs decomposed (NFD) must hash equal.
    nfc = unicodedata.normalize("NFC", "café\nbody")
    nfd = unicodedata.normalize("NFD", "café\nbody")
    assert nfc != nfd  # sanity: they differ as strings
    assert canonical_body_hash(nfc) == canonical_body_hash(nfd)


def test_rstrip_per_line():
    a = "line one   \nline two\n"
    b = "line one\nline two\n"
    assert canonical_body_hash(a) == canonical_body_hash(b)


def test_collapse_three_blank_lines_to_two():
    sparse = "para1\n\n\n\npara2"
    dense = "para1\n\n\npara2"
    assert canonical_body_hash(sparse) == canonical_body_hash(dense)


def test_two_blank_lines_preserved():
    # collapse only triggers at 3+; 2 blank lines stay 2.
    a = "para1\n\n\npara2"
    assert canonical_body_hash(a) == canonical_body_hash("para1\n\n\npara2")


def test_strip_trailing_newlines():
    a = "body\n\n\n"
    b = "body"
    assert canonical_body_hash(a) == canonical_body_hash(b)


def test_empty_body_stable():
    h = canonical_body_hash("")
    assert len(h) == 64
    assert h == canonical_body_hash("\n\n\n")  # blanks collapse to empty