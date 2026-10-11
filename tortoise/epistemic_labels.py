"""The EPISTEMIC label vocabulary — the ONE place "which labels carry belief" is defined.

Owner ruling (#7813, applied by #7853): **confidence propagates across ``:Subject``,
``:Object``, ``:Event`` and ``:Point``.** Before this module the same concept was written out
ad hoc as ``(x:Point OR x:Event)`` in twelve places across seven files, so the codebase carried
**two notions of one concept** — a widening applied to one copy silently left the others behind.
Every site that asks "is this node epistemic?" must ask THIS module.

Why a module rather than a constant next to one caller: the vocabulary is needed by ``sdk``,
``projection.edges``, ``commit_ops`` and ``hosted_api`` — modules that cannot import each other
without a cycle — so the shared definition has to sit at a leaf. This module imports nothing.

The TWO SHAPES, and why there are two
-------------------------------------
The engine rejects a label expression, so the widening is a ``WHERE``-OR predicate — and where
that predicate sits decides whether it is a filter or a scan.

``MATCH (a:Subject|Object|Event|Point)-[r:IMPL]->(b)`` is **not valid Cypher here**. Measured on
FalkorDB 6.0.1 (``redis_version 8.10.2``, graph module ``ver 60001``)::

    Invalid input '|': expected ')', pos 16

(Neo4j 5 added label disjunction; FalkorDB has not.) So every site uses one of:

**1. :func:`epistemic_clause` — the OR predicate, for a node reached by TRAVERSAL.**
The clause is a *filter* on an already-anchored walk, so it costs essentially nothing, and the
label may simply be dropped from the pattern::

    MATCH (o:Point {is_operator:true})-[r:IMPL|NAND]->(c)
    WHERE o.id IN $ids AND (c:Point OR c:Object OR c:Event OR c:Subject)

**2. :func:`epistemic_anchored_union` — a UNION ALL of label-anchored lookups, for a node
addressed BY ID (or any query whose only anchor would otherwise be the label itself).**
``_ensure_indexes`` creates a separate index per ``(label, property)``, so the disjunctive form has
nothing to anchor on and the planner falls back to a whole-graph scan. Measured on this host,
8,400 nodes / 1,200 ``IMPL`` edges, 30 runs after 5 warmups:

===============================  =========  =========
shape                            p50        p95
===============================  =========  =========
``MATCH (n) WHERE (n:Point OR n:Event) AND n.id IN $ids``   4.118 ms   6.746 ms
``UNION ALL`` of label-anchored id lookups                  1.823 ms   3.127 ms
===============================  =========  =========

⇒ **2.3× faster**, and ``search_engine.py`` carries the same finding independently (~5.9 ms whole-
graph scan vs ~0.5 ms label-anchored at 9k nodes) from before this change.

⚠️ **A WRITE cannot use shape 2** (there is no ``UNION`` of ``CREATE``/``MERGE``). A write that must
address a node by id and admit all four labels therefore has to **resolve the label first** and then
issue a label-anchored write — a follow-up, deliberately not done here, because it changes write-path
behaviour rather than consolidating a definition.

⚠️ **What is REACHABLE today.** The write path is asymmetric: ``create_operator`` accepts endpoints as
``(s:Point OR s:Event)`` while ``create_direct_edge`` still validates ``Point`` only. So ``:Event``
endpoints are writable, and ``:Subject`` / ``:Object`` operator endpoints are **forward-looking** per
the ruling — admitted here because the ruling says the concept has four members, but not yet produced
by any writer. This module encodes the ruling; it does not by itself make those endpoints reachable,
and the ``:Point``-only ``ep.py`` traversal sites are a SEPARATE widening (#7853's remaining half).

Where the concept lives: :data:`EPISTEMIC_LABELS`. Relation: these are the node labels on the
epistemic path — distinct from ``security.ENTITY_TYPE_LABELS`` (a query-surface name→label map) and
from ``taxonomy.taxonomy`` (a counting surface, which also counts ``:Source``).
"""
from __future__ import annotations

#: The labels that carry belief, in anchor order (``Point`` first: it is the operator label and the
#: commonest child). The single source for every "is this node epistemic?" test on a query path.
#:
#: Ordered, not a frozenset: the order is the emitted predicate's order, so the generated Cypher is
#: stable and diffable rather than dependent on set iteration.
EPISTEMIC_LABELS: tuple[str, ...] = ("Point", "Object", "Event", "Subject")

#: The epistemic labels a WRITE may connect an operator to — the admissible ENDPOINT set, which is
#: NARROWER than :data:`EPISTEMIC_LABELS` and is a **different concept**, not a duplicate of it.
#:
#: ``create_operator`` validates its endpoints as ``(s:Point OR s:Event)`` while
#: ``create_direct_edge`` still validates ``Point`` only, so ``:Subject`` / ``:Object`` endpoints
#: cannot be WRITTEN today even though the ruling says they carry belief. Keeping the two sets
#: separate is what stops a read-widening from silently changing which nodes a MERGE resolves —
#: before this constant existed, twelve inline pairs made the two sets indistinguishable.
#:
#: ⛔ Widening THIS set to :data:`EPISTEMIC_LABELS` is the open owner question on #7853 (it changes
#: what the write path accepts and touches the storage ruling) — do not widen it as a tidy-up.
EPISTEMIC_ENDPOINT_LABELS: tuple[str, ...] = ("Point", "Event")


def _clause(var: str, labels: tuple[str, ...]) -> str:
    if not var:
        raise ValueError("epistemic label clause needs a variable name")
    return "(" + " OR ".join(f"{var}:{label}" for label in labels) + ")"


def endpoint_clause(var: str) -> str:
    """The predicate admitting every label an operator ENDPOINT may be written as.

    Use this on a query that DECIDES OR PERFORMS A WRITE (a ``CREATE``/``MERGE`` endpoint, or an
    existence gate that chooses whether to stub, skip or link). Use :func:`epistemic_clause` on a
    pure READ. The distinction is the whole reason both exist — see
    :data:`EPISTEMIC_ENDPOINT_LABELS`.
    """
    return _clause(var, EPISTEMIC_ENDPOINT_LABELS)


def epistemic_clause(var: str) -> str:
    """The Cypher predicate admitting every epistemic label on ``var`` — shape 1.

    For a node reached by TRAVERSAL, where the clause is a filter and the label can be dropped from
    the pattern. The result is PARENTHESISED, so it is safe to join with ``AND``/``OR``::

        f"MATCH (o:Point {{is_operator:true}})-[r:IMPL|NAND]->(c) "
        f"WHERE o.id IN $ids AND {epistemic_clause('c')}"

    ``var`` is interpolated verbatim, so pass a bare Cypher identifier — never user input.
    """
    return _clause(var, EPISTEMIC_LABELS)


def epistemic_anchored_union(var: str, id_expr: str, ret: str) -> str:
    """A ``UNION ALL`` of label-anchored lookups on ``id_expr`` — shape 2.

    For a node addressed BY ID, where a disjunctive label test has nothing to anchor on and the
    planner would scan the whole graph (see the module docstring for the measurement). Each leg is
    anchored on ``(var:Label)`` so it uses that label's ``id`` index, and each projects the SAME
    column name so the union is well-formed. Duplicates cannot arise: a node carries one of these
    labels, so at most one leg matches it.

    ``var``, ``id_expr`` and ``ret`` are interpolated verbatim — hence ``noqa: S608`` at the callers'
    bandit gate; pass Cypher text you control, never user input::

        epistemic_anchored_union("n", "$ids", "n.id")   # ... plus the caller's own WHERE
    """
    if not var or not id_expr or not ret:
        raise ValueError("epistemic_anchored_union needs var, id_expr and ret")
    return " UNION ALL ".join(
        f"MATCH ({var}:{label}) WHERE {id_expr} RETURN {ret}" for label in EPISTEMIC_LABELS)
