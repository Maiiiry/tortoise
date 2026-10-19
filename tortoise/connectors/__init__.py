"""P1-5: External connectors.

Connectors poll external services and derive EventRecorded events
for the Tortoise event log. Uses `gh` CLI for GitHub — no PyGithub needed.
"""
from __future__ import annotations

#: "the caller did not hand us an already-resolved approval set" — a private
#: sentinel, NOT ``None``: ``None`` is the RESOLVED answer "this graph has no
#: ``:PackInstall`` records" (the catalog union ⇒ no gate). A default that
#: doubled as both would either skip the gate on the second batch or pay the
#: whole-graph read twice.
_READ_THE_SET: object = object()


def _gate_kinds(proj, records, *, writer: str,
                approved=_READ_THE_SET) -> object:
    """#9068: the approved-pack gate for a connector's fold batch.

    Connectors fold records with a bare ``proj.apply(...)`` instead of going
    through ``TortoiseSDK``'s write funnels, so they never met the #5475 door.
    ``FalkorProjection.apply`` refuses a direct fold only on a JOURNAL-BACKED
    projection (§17.1's single-writer guard) — and there the cache is a derived
    image of the journal. On a journal-less projection the cache IS the store,
    so "the journal is the truth, replay must be faithful" does not apply and
    the fold is a real write with nothing in front of it. Measured before this
    gate: ``github``'s ``object_kind='pm:issue'`` put ``pm`` into the approved
    set of a graph whose only ``:PackInstall`` record is ``dev``.

    One call per BATCH, before the first ``apply`` — see
    ``pack_state.refuse_unapproved_kinds_in_records`` for why the batch form
    (not one read per record) is the shape both correctness and cost require.

    Returns the approval set it decided against, so a connector that gates two
    batches in ONE pass (github: the entity chains, then the poll events) pays
    one whole-graph read and hands the result forward as ``approved=``. A batch
    whose kinds name NO pack namespace returns ``_READ_THE_SET`` unchanged
    (see the pre-check below), so the next call decides for itself instead of
    inheriting a read it never made.
    """
    from tortoise.pack_state import (
        record_names_a_pack,
        refuse_unapproved_kinds_in_records,
        resolved_pack_set_from_graph,
    )

    if approved is _READ_THE_SET:
        # ⚠️ THE CHEAP PRE-CHECK (#9068 review): the resolution below is
        # ``_read_installs`` + ``_read_selection`` + ``graph_kind_namespaces``
        # — 8 whole-graph property scans, O(nodes). The WEBHOOK paths call this
        # with ONE record per delivered message, so the "one read per batch"
        # saving is zero there and a Slack channel at message rate would
        # serialise ~9 All-Node scans per message behind one socket. A batch
        # that names no pack namespace (the overwhelming majority: connector
        # records carry BARE ``sourceKind``/``eventKind`` spellings) cannot be
        # refused by the loop below, so skipping the read changes no answer —
        # it only stops the common case from paying for it. The predicate is
        # ``pack_state``'s own, so this can never screen a narrower key set
        # than the gate it short-circuits.
        approved = resolved_pack_set_from_graph(proj.g).namespaces
    refuse_unapproved_kinds_in_records(proj.g, records, writer=writer,
                                      approved=approved)
    return approved
