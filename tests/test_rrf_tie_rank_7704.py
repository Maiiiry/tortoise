"""#7704 — RRF must not turn a leg's TIE into a relevance difference.

The measured defect this file pins (embedded lane, ``origin/main`` at
``41e958de8``, dense leg live)::

    from tortoise.search_engine import rrf_fusion
    rrf_fusion([[("b", 1.0), ("a", 1.0)], [("b", 1.0), ("a", 1.0)]],
               strategy_names=["fts", "vector"], weights={"vector": 1.5})
    # main  -> {'b': 0.0409836, 'a': 0.0403226}   two documents with IDENTICAL
    #         scores on every leg are given DIFFERENT fused relevance, ordered
    #         by the leg's ``ORDER BY score DESC, id ASC`` tie-break (#3019)
    # fix   -> {'a': 0.0406504, 'b': 0.0406504}   identical evidence, identical
    #         relevance; the fused ``(-score, id)`` tie-break still orders them

That one-rank difference is what defeats the W4-b contested boost on a fused
read (#7704): a rank gap the boost cannot clear, created by a tie-break that
carries no evidence.  The same shortcut is why the SINGLE-LEG path already
behaves correctly — ``tortoise_fts_query`` skips ``rrf_fusion`` for one leg,
so two tied rows keep the same score there.

These tests are the decision instrument for #7704's option 5 (tie-averaged
ranks): they are RED on main and GREEN with the prototype on branch
``feat/7704-tie-measure``.  Nothing here asserts the *shipped* semantics —
the ruling on whether a leg's tie may spread documents apart is the owner's
(#7704), and the branch is a draft until it lands.

⛔ Option 5 is NOT free, and the reason is in this file's own evidence: it
makes every coarse-valued leg an atomic block, which (a) breaks
``temporal_leg``'s documented tail-placement guarantee (that leg encodes rank
by list position and passes 0.0 for every row), and (b) shrinks a heavily
tied leg's fusion mass by the convexity gap, touching the #1657 / #8488
relative-weight design.  See #7704 for the decision.
"""
from __future__ import annotations

from tortoise.search_engine import rrf_fusion


def test_identical_leg_scores_must_not_produce_different_fused_scores():
    """Two documents, identical score on EVERY leg => identical fused score.

    Mutation: enumerate() the leg positionally (the shipped code) → this reds
    with 0.0409836 vs 0.0403226, a 1.6% relative gap from zero evidence.
    """
    fused = rrf_fusion(
        [[("b", 1.0), ("a", 1.0)], [("b", 1.0), ("a", 1.0)]],
        strategy_names=["fts", "vector"],
        weights={"vector": 1.5},
    )
    assert fused["a"] == fused["b"], (
        "two rows the legs scored identically were given different fused "
        f"relevance: {fused}")


def test_a_tie_does_not_outrank_a_document_the_legs_ranked_better():
    """The tied pair must not SPREAD, and the tied pair's order stays id ASC.

    Scope note (review correction): in this fixture ``z`` is last under BOTH
    semantics, so the assertion below does not, by itself, discriminate — the
    interleaving the prototype removes lives in the #8488 fixture, where the
    four dense-tied rows score 0.040719 / 0.040067 / 0.039435 / 0.038822 and
    the bm25 winner slots in at index 2. What this test does pin is that
    tie-averaging does not disturb a distinct order and does not move ``z``.
    """
    fused = rrf_fusion(
        [[("b", 1.0), ("a", 1.0), ("z", 0.0)],
         [("b", 1.0), ("a", 1.0), ("z", 0.0)]],
        strategy_names=["fts", "vector"],
        weights={"vector": 1.5},
    )
    assert fused["a"] == fused["b"]
    assert min(fused["a"], fused["b"]) > fused["z"]
    # The tied pair keeps a deterministic, content-derived order (#2952).
    assert list(fused) == ["a", "b", "z"]


def test_a_generator_leg_is_not_dropped():
    """Materialising a leg twice must not swallow a generator (review P2).

    The shipped loop enumerated in ONE pass, so a caller passing an iterator
    worked; indexing it and then zipping the same iterator would return {}. No
    current caller does this, but the failure mode is total, silent result
    loss — so the prototype materialises once.
    """
    gen = ((pid, sc) for pid, sc in [("b", 2.0), ("a", 1.0), ("z", 0.0)])
    fused = rrf_fusion([gen], strategy_names=["fts"], weights=None)
    assert list(fused) == ["b", "a", "z"]


def test_a_distinct_leg_order_is_still_respected():
    """Tie-averaging must not flatten a leg that DID distinguish the rows."""
    fused = rrf_fusion(
        [[("top", 2.0), ("mid", 1.0), ("low", 0.0)]],
        strategy_names=["fts"],
        weights=None,
    )
    assert list(fused) == ["top", "mid", "low"]
    assert fused["top"] > fused["mid"] > fused["low"]
