"""Validate CollisionDetector — the engine's collision detection.

``subject = title > first H1 > None`` normalized (NFC + casefold + strip +
whitespace collapse); ``fact_id = SHA-256(subject)[:32]`` (128-bit hex). Two
current-state episodes of the same subject are a detectable collision UNLESS
they share a ``supersedes`` chain (revalidate / improve within the chain is not
a concurrent collision).
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from seahorse.engine.collision import CollisionDetector, fact_id_for
from tests.engine.conftest import _episode


class _FakeRepo:
    """Controlled repo for detect branches: returns a fixed current-state + chain."""

    def __init__(self, *, vigent=None, chain=None) -> None:
        self._vigent = vigent
        self._chain = chain or []

    def find_vigent_by_fact_id(self, fact_id: str, exclude: str | None = None):
        if self._vigent is None:
            return None
        if exclude is not None and self._vigent.id == exclude:
            return None
        return self._vigent

    def chain_from(self, ep_id: str):
        return self._chain

    def get(self, ep_id: str):
        return None

    @contextmanager
    def atomic(self) -> Iterator[None]:
        yield


# --- derive_subject -----------------------------------------------------------


def test_derive_subject_prefers_title_over_h1():
    body = "# Body Heading\ncontent"
    assert CollisionDetector.derive_subject(body, title="The Title") == "the title"


def test_derive_subject_falls_back_to_first_h1():
    body = "intro\n# First Heading\nmore\n# Second Heading"
    assert CollisionDetector.derive_subject(body, title=None) == "first heading"


def test_derive_subject_returns_none_when_no_title_and_no_h1():
    assert CollisionDetector.derive_subject("plain text\nno heading", title=None) is None


def test_derive_subject_returns_none_when_empty_title_and_no_h1():
    assert CollisionDetector.derive_subject("plain text", title="   ") is None


def test_derive_subject_h2_is_not_h1():
    # "## Sub" is an H2, not an H1 — must not be picked as the subject.
    assert CollisionDetector.derive_subject("## Sub\nbody", title=None) is None


def test_derive_subject_nfc_normalization():
    # Precomposed vs decomposed é yield the same subject.
    pre = CollisionDetector.derive_subject("# café\n", title=None)
    decomp = CollisionDetector.derive_subject("# café\n", title=None)
    assert pre == decomp == "café".casefold()


def test_derive_subject_casefold_and_whitespace_collapse():
    s = CollisionDetector.derive_subject("#   Hello    World  \n", title=None)
    assert s == "hello world"


# --- fact_id_for -------------------------------------------------------------


def test_fact_id_is_32_hex_chars():
    fid = fact_id_for("# Subject\nbody", title=None)
    assert len(fid) == 32
    int(fid, 16)


def test_fact_id_deterministic():
    a = fact_id_for("# Same Subject\nbody", title=None)
    b = fact_id_for("# Same Subject\nbody", title=None)
    assert a == b


def test_fact_id_none_when_subject_none():
    assert fact_id_for("no heading here", title=None) is None


def test_fact_id_differs_across_subjects():
    assert fact_id_for("# A\n", title=None) != fact_id_for("# B\n", title=None)


# --- detect ------------------------------------------------------------------


def test_detect_no_subject_returns_empty():
    ep = _episode(body="no heading", title=None)
    assert CollisionDetector().detect(ep, _FakeRepo()) == []


def test_detect_no_vigente_returns_empty():
    ep = _episode(body="# Subject\n", title=None)
    assert CollisionDetector().detect(ep, _FakeRepo(vigent=None)) == []


def test_detect_concurrent_collision():
    existing = _episode("e-old", body="# Subject\n", fact_id="irrelevant")
    ep = _episode("e-new", body="# Subject\n", title=None)
    repo = _FakeRepo(vigent=existing)
    collisions = CollisionDetector().detect(ep, repo)
    assert len(collisions) == 1
    assert collisions[0].kind == "concurrent"
    assert collisions[0].existing_id == "e-old"


def test_detect_excludes_self():
    # Same id as the only current-state → no collision with itself.
    ep = _episode("e-self", body="# Subject\n", title=None)
    repo = _FakeRepo(vigent=ep)
    assert CollisionDetector().detect(ep, repo) == []


def test_detect_same_chain_is_not_collision():
    # existing E2 is in the chain of new_ep.supersedes (E1) → same chain, not
    # concurrent. The chain exemption is op-gated: only improve invalidates its
    # supersedes target, so only improve may exempt a chain rival.
    e1 = _episode("e1", body="# Subject\n")
    e2 = _episode("e2", body="# Subject\n")
    new_ep = _episode("e-new", body="# Subject\n", supersedes="e1")
    repo = _FakeRepo(vigent=e2, chain=[e1, e2])
    assert CollisionDetector().detect(new_ep, repo, op="improve") == []


def test_detect_same_chain_active_rival_is_collision_on_apply_fact():
    # apply_fact treats supersedes as a SOFT reference (merge — the sources
    # remain valid): a chain rival that is still active HOLDS the fact_id slot,
    # so it must be reported. Exempting it let the uq_one_active_per_subject
    # backstop fire as a raw IntegrityError instead (loop L6b re-run,
    # 2026-09-02: the untagged rival chosen as the cluster's representative).
    e1 = _episode("e1", body="# Subject\n")
    e2 = _episode("e2", body="# Subject\n")
    new_ep = _episode("e-new", body="# Subject\n", supersedes="e1")
    repo = _FakeRepo(vigent=e2, chain=[e1, e2])
    collisions = CollisionDetector().detect(new_ep, repo)
    assert len(collisions) == 1
    assert collisions[0].existing_id == "e2"


def test_detect_different_chain_is_collision():
    other = _episode("e-other", body="# Subject\n")
    new_ep = _episode("e-new", body="# Subject\n", supersedes="e1")
    # chain of e1 does NOT contain e-other → concurrent collision.
    e1 = _episode("e1", body="# Other\n")
    repo = _FakeRepo(vigent=other, chain=[e1])
    collisions = CollisionDetector().detect(new_ep, repo)
    assert len(collisions) == 1
    assert collisions[0].existing_id == "e-other"