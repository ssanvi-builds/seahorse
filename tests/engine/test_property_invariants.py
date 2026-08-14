"""Property-based bi-temporal invariants — a regression safety net.

Hypothesis stateful machine that drives the real persistence stack
(``ConnectionManager`` + migrations + ``SqliteEpisodeRepository`` /
``SqliteAuditEventRepository``) through arbitrary sequences of
``remember`` / ``improve`` / ``forget`` with random ``valid_at`` offsets, then
asserts the bi-temporal invariants hold over the stored state after every op.

These are NOT feature tests (RED-then-GREEN); the engine is already correct.
They are a regression safety net so refactors (Retriever extraction,
lazy-imports, audit-inside-atomic, state_at NULL alignment) cannot silently
break the bi-temporal contract. Run them with ``-m property``; they run in the
normal suite.

Why ``RuleBasedStateMachine`` and not ``@given``: each generated test case needs
a FRESH database. A function-scoped pytest fixture is shared across all
``@given`` examples of one call, so state would accumulate and pollute later
examples. The stateful machine calls ``__init__`` (fresh temp DB) once per test
case, which is the correct isolation boundary.

Ops use ``source_type="human"`` so the valid_at guard admits arbitrary
``valid_at`` (past / future / null); ``agent`` would restrict ``valid_at`` to
null-or-now and starve the PENDING / past-valid_at branches we want to exercise.
Bodies are drawn from a small finite set so collisions and fact_id reuse occur
naturally. ``now`` advances by a fixed step per op (monotonic transaction time,
mirrors deployment). ``improve`` / ``forget`` address ep_ids by index into the
list of accepted creates, modulo its length (the common dependent-op pattern
that avoids a runtime-dependent strategy).

References:
- valid_at null = "from forever" (valid at any now)
- PENDING_INGEST = valid_at in the future; is_valid_at(now) is False until now >= valid_at
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from hypothesis import HealthCheck, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, rule

from seahorse.contracts.engine import InvalidationConflictError, NotFound
from seahorse.engine.engine import BiTemporalEngine
from seahorse.engine.errors import EngineError
from seahorse.persistence.connection import ConnectionManager
from seahorse.persistence.migrations.migrator import apply_migrations
from seahorse.persistence.sqlite_audit import SqliteAuditEventRepository
from seahorse.persistence.sqlite_episode_repo import SqliteEpisodeRepository

# Module-level marker so ``-m property`` selects (or ``-m "not property"``
# excludes) the whole suite without decorating the generated TestCase.
pytestmark = pytest.mark.property

NOW0 = datetime(2026, 1, 1, tzinfo=UTC)
STEP = timedelta(hours=1)

# Small body set so collisions (same subject+fact_id) and fact_id reuse happen
# naturally across ops.
BODIES = ("alpha note", "beta note", "gamma note", "delta note")

# human admits arbitrary valid_at (past/future/null); agent would restrict to
# null-or-now and starve the PENDING / past branches.
BY = {"source_type": "human", "agent_id": "prop-test"}

# valid_at offset in STEP units relative to the op's now. None = "from forever".
_VOFF = st.one_of(st.none(), st.integers(min_value=-3, max_value=5))

# Ops the engine legitimately rejects (guard / collision / NotFound / already
# invalidated). State is unchanged for these (atomicity), so rejected ops do
# not perturb the invariants — we just skip them.
_REJECTED = (NotFound, InvalidationConflictError, EngineError)


@settings(
    max_examples=40,
    stateful_step_count=12,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large],
)
class BiTemporalInvariantMachine(RuleBasedStateMachine):
    """Drives remember/improve/forget over a fresh DB; asserts bi-temporal invariants."""

    def __init__(self) -> None:
        super().__init__()
        self._tmp = Path(tempfile.mkdtemp(prefix="seahorse-prop-"))
        mgr = ConnectionManager(self._tmp / "seahorse.db", pool_size=2, extensions=("vec0",))
        mgr.open()
        apply_migrations(mgr.writer)
        self._mgr = mgr
        self._repo = SqliteEpisodeRepository(mgr)
        self._audit = SqliteAuditEventRepository(mgr)
        self._engine = BiTemporalEngine(self._repo, self._audit)
        self._now = NOW0
        # ep_ids of every accepted remember/improve create, in order. This is the
        # complete set of stored ep_ids (forget keeps the row; it never removes).
        self._remembered: list[str] = []

    def teardown(self) -> None:
        self._mgr.close()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---- helpers ------------------------------------------------------------

    def _stored(self) -> list:
        """All episodes created by accepted ops, in creation order (None dropped)."""
        eps = []
        for eid in self._remembered:
            ep = self._repo.get(eid)
            if ep is not None:
                eps.append(ep)
        return eps

    @staticmethod
    def _valid_at(voff: int | None, now: datetime) -> datetime | None:
        return None if voff is None else now + voff * STEP

    # ---- rules (mutators) ----------------------------------------------------

    @rule(body=st.sampled_from(BODIES), voff=_VOFF)
    def remember(self, body: str, voff: int | None) -> None:
        self._now = self._now + STEP
        va = self._valid_at(voff, self._now)
        try:
            wr = self._engine.remember(body=body, by=BY, valid_at=va, now=self._now)
        except _REJECTED:
            return
        if wr.status in ("ACTIVE", "PENDING_INGEST"):
            self._remembered.append(wr.ep_id)
        # COLLISION / NOOP -> no append, do not record.

    @rule(idx=st.integers(min_value=0, max_value=7), body=st.sampled_from(BODIES), voff=_VOFF)
    def improve(self, idx: int, body: str, voff: int | None) -> None:
        if not self._remembered:
            return
        self._now = self._now + STEP
        target = self._remembered[idx % len(self._remembered)]
        va = self._valid_at(voff, self._now)
        try:
            new_ep = self._engine.improve(target, body, by=BY, valid_at=va, now=self._now)
        except _REJECTED:
            return
        self._remembered.append(new_ep.id)

    @rule(idx=st.integers(min_value=0, max_value=7))
    def forget(self, idx: int) -> None:
        if not self._remembered:
            return
        self._now = self._now + STEP
        target = self._remembered[idx % len(self._remembered)]
        try:
            self._engine.forget(target, reason="prop", by=BY, now=self._now)
        except _REJECTED:
            return

    # ---- invariants (asserted after every rule) -----------------------------

    @invariant()
    def vigente_matches_predicate(self) -> None:
        """get_vigente(now) == {stored eps not invalidated/expired AND valid at now}.

        Soundness AND completeness: every returned ep satisfies the predicate,
        and every stored ep satisfying the predicate is returned. ``valid_at IS
        NULL`` ("from forever") IS valid at any now.
        """
        now = self._now
        expected = {
            ep.id
            for ep in self._stored()
            if ep.invalid_at is None
            and ep.expired_at is None
            and (ep.valid_at is None or ep.valid_at <= now)
        }
        actual = {ep.id for ep in self._engine.get_vigente(now=now)}
        assert actual == expected, f"current-state mismatch at {now}: {actual} != {expected}"

    @invariant()
    def is_valid_at_matches_predicate(self) -> None:
        """is_valid_at(ep, t) == (valid_at None or <= t) AND (invalid_at None or t < invalid_at).

        Checked at now, before-now, and after-now so past/future/now boundaries
        are all probed for every stored ep.
        """
        now = self._now
        for ep in self._stored():
            for t in (now, now - 10 * STEP, now + 10 * STEP):
                expected = (ep.valid_at is None or ep.valid_at <= t) and (
                    ep.invalid_at is None or t < ep.invalid_at
                )
                got = self._engine.is_valid_at(ep.id, t)
                assert got == expected, (
                    f"is_valid_at({ep.id}, {t})={got} != {expected} "
                    f"(valid_at={ep.valid_at}, invalid_at={ep.invalid_at})"
                )

    @invariant()
    def vigente_and_is_valid_at_agree(self) -> None:
        """Bulk and single PIT predicates must agree (drift guard).

        For every stored ep with ``expired_at IS NULL`` (always true in the
        current release), membership in ``get_vigente(now)`` must equal
        ``is_valid_at(ep, now)``. These are two independent code paths (the bulk
        ``query_vigent`` + reader post-filter vs. the single-ep predicate); a
        divergence between them is exactly the class of bug where ``is_valid_at``
        included ``valid_at IS NULL`` while the bulk state_at predicates
        excluded it. This cross-check is the highest-value invariant in the
        suite: the per-method predicate invariants above are near-tautological
        (they re-derive each method's own predicate), but this one cross-validates
        two methods against each other, so a one-sided drift cannot pass.
        """
        now = self._now
        vigente_ids = {ep.id for ep in self._engine.get_vigente(now=now)}
        for ep in self._stored():
            if ep.expired_at is not None:
                continue  # decay path deferred to a later release; out of scope for this net
            membership = ep.id in vigente_ids
            validity = self._engine.is_valid_at(ep.id, now)
            assert membership == validity, (
                f"predicate drift on {ep.id}: in_vigente={membership} "
                f"is_valid_at={validity} (valid_at={ep.valid_at}, "
                f"invalid_at={ep.invalid_at})"
            )

    @invariant()
    def supersedes_chain_reflexive(self) -> None:
        """Every ep is in its own bidirectional supersedes closure."""
        for ep in self._stored():
            chain_ids = {x.id for x in self._engine.follow_supersedes_chain(ep.id)}
            assert ep.id in chain_ids, f"{ep.id} not in its own chain {chain_ids}"

    @invariant()
    def supersedes_chain_bidirectional(self) -> None:
        """If A supersedes B (both stored), A in chain(B) and B in chain(A)."""
        for ep in self._stored():
            if ep.supersedes is None:
                continue
            if self._repo.get(ep.supersedes) is None:
                continue  # dangling ref should not occur (guard), skip defensively
            my_chain = {x.id for x in self._engine.follow_supersedes_chain(ep.id)}
            tgt_chain = {x.id for x in self._engine.follow_supersedes_chain(ep.supersedes)}
            assert ep.supersedes in my_chain, f"{ep.supersedes} not in chain({ep.id})"
            assert ep.id in tgt_chain, f"{ep.id} not in chain({ep.supersedes})"

    @invariant()
    def stored_state_monotonic(self) -> None:
        """valid_at <= invalid_at and created_at <= expired_at (when both non-null)."""
        for ep in self._stored():
            if ep.valid_at is not None and ep.invalid_at is not None:
                assert ep.valid_at <= ep.invalid_at, (
                    f"monotonicity violated on {ep.id}: "
                    f"valid_at={ep.valid_at} > invalid_at={ep.invalid_at}"
                )
            if ep.expired_at is not None and ep.created_at is not None:
                assert ep.created_at <= ep.expired_at, (
                    f"monotonicity violated on {ep.id}: "
                    f"created_at={ep.created_at} > expired_at={ep.expired_at}"
                )


TestBiTemporalInvariants = BiTemporalInvariantMachine.TestCase