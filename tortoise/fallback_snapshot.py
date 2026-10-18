"""In-memory degraded-fallback corpus snapshot (#1375).

The degraded path (all retrieval strategies failed → in-memory TF-IDF) used to
re-fetch the whole corpus with FULL payloads via ``self.query`` (~350ms-2s on
Docker for ~1000 points) and re-encode/re-fit per call (8-700ms variable).
This module keeps a LEAN projection (id/content/pointKind/status) plus cached
document vectors, invalidated by a dirty flag on the write surfaces
(``_mark_dirty`` hook — one hook covers create/update/supersede/retract/
operator/mitigation/delete/ingest/dream) with a LAZY TTL backstop (age check
at read — zero steady-state cost; logs when it fires while clean, signalling
a write bypassed the normal surfaces).

Vector parity: when an embedding model is present, doc vectors come from
``model.encode(texts)`` (cached) and the query is encoded per call — same
model, same texts, so rankings match the legacy neural path (the batch-vs-
separate encode caveat is documented; the sklearn path is byte-identical to
legacy per the #399 fit-on-docs/transform-query contract and is what the
parity test verifies).

Mirrors the retrieval layer's exclusions: non-operators, retracted and
``outdated=true`` points are excluded at build; terminal-status points
(retracted/superseded/outdated/archived/deprecated) are filtered at serve
unless ``include_terminal`` (the #1391 contract); ``kind`` and
``exclude_status`` compose on top.

Size cap: above MAX_CORPUS_POINTS the snapshot is skipped (hosted OOM
protection) and the legacy path runs unchanged. The cached snapshots themselves
are bounded by BOTH an entry count and an estimated resident-byte budget
(MAX_SNAPSHOT_ENTRIES / MAX_SNAPSHOT_BYTES, LRU-evicted) — an entry count alone
does not measure the resource at risk (#7773).
"""
from __future__ import annotations

import logging
import os
import sys
import threading
import time

logger = logging.getLogger(__name__)

MAX_CORPUS_POINTS = 50_000
SNAPSHOT_TTL_SECONDS = 60.0

# Bound the process-global cache. With per-backend keys the store is no longer
# bounded by the number of (graph_name, namespace) pairs a process holds, so a
# long-lived process that opens many distinct embedded DBs (or :memory: stores)
# would otherwise retain one corpus each (#7760 review). LRU: a fresh ``get``
# bumps an entry, so a hot store survives — the hosted per-request-SDK pattern
# must keep its one graph's snapshot across requests.
MAX_SNAPSHOT_ENTRIES = 8

# ... and the ENTRY count is not the resource at risk (#7773). One entry can
# hold MAX_CORPUS_POINTS points AND, when an embedder is present, a dense
# float64 doc-vector matrix (~150 MB at 384 dims) — 8 such entries is ~1.2 GB,
# while this store exists to protect a hosted process from OOM. So the store is
# ALSO bounded by estimated resident bytes (``_snapshot_bytes``), evicted LRU
# while either cap is exceeded. 256 MiB admits one full-size dense corpus (the
# hottest, ~165 MB worst case) and caps the store far below the 1.2 GB the
# entry cap alone allowed.
#
# NOT a hard ceiling for a SINGLE entry: the snapshot just built is never
# evicted for exceeding the budget on its own (evicting it would re-fetch the
# corpus and re-fit TF-IDF on every degraded read — the #1375 cost this store
# exists to avoid — and would make an oversized store unusable rather than
# merely oversized). Worst case is therefore ONE oversized corpus, and that
# infeasibility is logged at WARNING rather than silently accepted.
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024

# Holds ALL non-operator points (incl. terminal statuses + outdated flag);
# the serve-time exclusion in search_snapshot mirrors self.query's two-mode
# semantics (#1391: terminal statuses + outdated excluded unless include_terminal).
_SNAPSHOT_QUERY = (
    "MATCH (n:Point) "
    "WHERE (n.is_operator = false OR n.is_operator IS NULL) "
    "RETURN n.id, n.content, n.pointKind, n.status, "
    "       coalesce(n.outdated, false), coalesce(n.search_keys, ''), "
    "       coalesce(n.has_answer, false)"
)


def _buffer_bytes(obj) -> int:
    """Resident bytes of a vector buffer: numpy dense, scipy sparse, else 0.

    ``nbytes`` covers an ``ndarray`` (the dense-embedder case, the ~150 MB term
    this bound exists for). A scipy sparse matrix carries its three buffers as
    separate arrays instead, so they are summed. Anything else (``None``, a
    duck-typed stand-in) contributes nothing — this is an estimate of the
    dominant terms, not a proof of the process's RSS.
    """
    nbytes = getattr(obj, "nbytes", None)
    if isinstance(nbytes, int):
        return nbytes
    total = 0
    for attr in ("data", "indices", "indptr"):
        arr = getattr(obj, attr, None)
        n = getattr(arr, "nbytes", None)
        if isinstance(n, int):
            total += n
    return total


def _snapshot_bytes(snap: dict) -> int:
    """Estimated resident bytes of ONE cache entry (#7773).

    Counts the lean point rows (each row's dict allocation plus the allocation
    of every leaf value: its id/content/search_keys strings), the doc-vector
    buffer, and — on the sklearn path — the vectorizer's vocabulary table and
    ``idf_`` array. Those are the terms that can exhaust a hosted process; the
    estimate is deliberately conservative-high rather than tight, because the
    budget it feeds is an OOM guard.

    Measured only on ``put`` (once per rebuild, itself a 350 ms-2 s corpus
    re-fetch), so the walk over at most ``MAX_CORPUS_POINTS`` rows never runs
    on the steady-state read path.
    """
    points = snap.get("points") or ()
    total = sys.getsizeof(points)
    for row in points:
        total += sys.getsizeof(row)
        for value in row.values():
            if isinstance(value, (str, bytes, int, float, bool)):
                total += sys.getsizeof(value)
    total += _buffer_bytes(snap.get("doc_vecs"))
    vectorizer = snap.get("vectorizer")
    if vectorizer is not None:
        vocab = getattr(vectorizer, "vocabulary_", None)
        if isinstance(vocab, dict):
            total += sys.getsizeof(vocab)
            for term, index in vocab.items():
                total += sys.getsizeof(term) + sys.getsizeof(index)
        total += _buffer_bytes(getattr(vectorizer, "idf_", None))
    return total


class FallbackSnapshotStore:
    """Thread-safe LRU snapshot store keyed by ``snapshot_key``.

    Bounded by BOTH ``MAX_SNAPSHOT_ENTRIES`` and ``MAX_SNAPSHOT_BYTES``. The
    per-backend key is not bounded by the (graph_name, namespace) pair count
    the way the old key was (#7760 review), and an ENTRY count does not measure
    the resource at risk: one entry can hold ``MAX_CORPUS_POINTS`` points plus
    a ~150 MB dense doc-vector matrix, so 8 entries is ~1.2 GB while a hosted
    process may have less (#7773). ``_sizes`` mirrors ``_store`` with each
    entry's estimated bytes, so eviction never re-measures the corpus and a
    rebuilt entry replaces its own size record.
    """

    def __init__(self) -> None:
        self._store: dict[tuple, dict] = {}
        # Estimated resident bytes per key, mirroring ``_store`` exactly. Kept
        # in the store rather than on the snapshot so the cached payload stays
        # exactly the shape ``build_snapshot`` returns.
        self._sizes: dict[tuple, int] = {}
        self._lock = threading.RLock()

    def invalidate(self, key: tuple) -> None:
        with self._lock:
            self._forget(key)

    def get(self, key: tuple) -> dict | None:
        """Fresh snapshot or None (missing / dirty / TTL-expired)."""
        now = time.monotonic()
        with self._lock:
            snap = self._store.get(key)
            if snap is None:
                return None
            if snap.get("dirty"):
                self._forget(key)
                return None
            if now - snap["built_at"] > SNAPSHOT_TTL_SECONDS:
                logger.warning(
                    "Fallback snapshot %r TTL-fired while clean — a write may "
                    "have bypassed the normal write surfaces", key,
                )
                self._forget(key)
                return None
            self._store.pop(key, None)  # LRU bump — keep a hot store
            self._store[key] = snap
            return snap

    def put(self, key: tuple, snap: dict) -> None:
        with self._lock:
            self._store.pop(key, None)  # re-insert so order is by recency
            self._store[key] = snap
            self._sizes[key] = _snapshot_bytes(snap)
            self._evict_over_capacity(newest=key)

    def _forget(self, key: tuple) -> None:
        """Drop ``key`` from both maps together — they must never desync.

        Caller holds ``self._lock``.
        """
        self._store.pop(key, None)
        self._sizes.pop(key, None)

    def _total_bytes(self) -> int:
        """Caller holds ``self._lock``."""
        return sum(self._sizes.values())

    def _evict_over_capacity(self, *, newest: tuple) -> None:
        """LRU-evict while EITHER cap is exceeded; never evict ``newest``.

        ``newest`` is exempt because dropping the snapshot just built would
        re-fetch the corpus and re-fit TF-IDF on every degraded read (the
        #1375 cost this store exists to avoid), and would make a store whose
        single corpus exceeds the byte budget useless rather than merely
        oversized. The byte budget is therefore a best-effort cap above ONE
        corpus; an entry that cannot be accommodated on its own is kept and
        reported at WARNING instead of being dropped silently.

        Caller holds ``self._lock``.
        """
        while len(self._store) > 1 and (
                len(self._store) > MAX_SNAPSHOT_ENTRIES
                or self._total_bytes() > MAX_SNAPSHOT_BYTES):
            # ``_store`` is insertion-ordered and ``newest`` was inserted last,
            # so the first key that is not ``newest`` is the LRU entry.
            victim = next(k for k in self._store if k != newest)
            freed = self._sizes.get(victim, 0)
            self._forget(victim)
            logger.info(
                "Fallback snapshot evicted %r (%d bytes) — over capacity: "
                "%d/%d entries, %d/%d bytes",
                victim, freed, len(self._store), MAX_SNAPSHOT_ENTRIES,
                self._total_bytes(), MAX_SNAPSHOT_BYTES,
            )
        if self._total_bytes() > MAX_SNAPSHOT_BYTES:
            logger.warning(
                "Fallback snapshot %r is ~%d bytes, over the %d-byte store "
                "budget on its own — retained (evicting it would re-fetch the "
                "corpus on every degraded read), so the store is bounded to "
                "one such corpus",
                newest, self._sizes.get(newest, 0), MAX_SNAPSHOT_BYTES,
            )

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._sizes.clear()


_store = FallbackSnapshotStore()

# Stable key for a ``:memory:`` projection whose client exposes no store
# identity handle at all. All such projections share it, which buys
# cacheability at the cost of per-store ISOLATION — not in one direction only:
# this same tuple also keys the READ path (``sdk._fb_store.get(_key)``), so the
# first such store to build caches its snapshot under it and a DIFFERENT store
# is then served that snapshot, with no write required. That is acceptable only
# because the branch is unreachable for a real client: a real embedded client
# always exposes ``socket_file`` (on ``db`` or ``db.client``), and only a
# client exposing no handle at all can land here — where two stores are
# indistinguishable, so the real choice is a shared cacheable key versus a
# per-call key that can never be hit. Reachability is nil today; if a real
# client ever stops exposing a handle, this key must be revisited.
_UNIDENTIFIED_MEMORY_STORE = "<unidentified-memory-store>"


def _embedded_store_identity(proj) -> str:
    """Identity of the embedded store a ``:memory:`` projection uses.

    redislite keys its running-daemon registry by ``<cwd>/:memory:.settings``,
    so every ``FalkorProjection`` over ``":memory:"`` in one CWD attaches to the SAME
    daemon and the SAME graph — the second sees the first's writes. The corpus
    identity must therefore be the STORE INSTANCE, not the projection: a
    per-projection token gives two projections over one store two keys, so
    ``_mark_dirty``'s ``invalidate`` reaches only the writer's key and a
    sibling's cached snapshot survives the write — a just-written point stays
    invisible and a just-deleted point is still served until the TTL (#7773
    P1).

    The registry path alone is only the SLOT, not the instance: a daemon that
    replaces a dead one at the same ``<cwd>/:memory:.settings`` starts a fresh
    (empty) graph, so a key on the slot matches the dead store's cached
    snapshot and serves phantom points (#7773 review). The live daemon's
    ``socket_file`` is preferred: every projection attached to one daemon
    shares it, and a replacement daemon gets a new temp socket, so the identity
    distinguishes instances while still unifying siblings. The registry path is
    the fallback for a client that exposes only that.

    When neither handle exposes a value, the key is a process-wide constant —
    stable, shared by every such projection, and logged LOUDLY — rather than
    re-minted per call. Sharing is NOT isolation-preserving: this tuple also
    keys the read path, so one such store's snapshot is served to another. It
    is safe only because the branch is unreachable for a real client — both a
    real ``FalkorProjection`` and redislite expose ``socket_file``, so no real
    store reaches here. The old code instead stamped a per-projection token and
    suppressed the failed assignment, so a client that REJECTS the stamp
    (``__slots__``/immutable) got a fresh key on every call: the entry could
    never be cache-hit and the corpus re-fetch + TF-IDF re-fit ran silently on
    every degraded search (#7773 P2).
    """
    db = getattr(proj, "db", None)
    handles = (db, getattr(db, "client", None))
    for attr in ("socket_file", "settingregistryfile"):
        for handle in handles:
            val = getattr(handle, attr, None)
            if isinstance(val, str) and val:
                return os.path.realpath(val)
    logger.error(
        "Fallback snapshot: the :memory: store identity is unavailable from "
        "the projection's client (no socket_file/settingregistryfile) — using "
        "the process-wide unidentified-store key. That key is cacheable but "
        "NOT isolated: every such store shares one snapshot slot, so one "
        "store's snapshot can be served to another on a read. A real client "
        "always exposes a socket_file, so this branch should be unreachable "
        "(#7773 P2).",
    )
    return _UNIDENTIFIED_MEMORY_STORE


def snapshot_key(proj, namespace: str | None) -> tuple:
    """Identity of the corpus a snapshot describes.

    ``graph_name`` alone does NOT identify an embedded store: every embedded DB
    defaults to ``'tortoise'``, so a store keyed on it alone served one DB's
    snapshot to another — the leaked point in #7760 (a test's degraded search
    returned a point written by a different test's SDK on a different file).
    The embedded file's realpath is the second half of the identity — the same
    reason #3049's ``_prewipe_graph_identity`` carries ``db_path``.

    A server/URI graph carries no file, so its identity IS its graph name
    (already the first element), matching #3049; a process holding two servers
    that carry the SAME graph name is out of contract here and still shares a
    slot (pre-existing, and unchanged by this key). ``:memory:`` carries no
    file either, but it is NOT a private store per projection: redislite
    resolves it to a shared embedded daemon whose identity is its live socket
    (``_embedded_store_identity``).
    """
    path = getattr(proj, "_path", None)
    if path == ":memory:":
        backend = ("memory", _embedded_store_identity(proj))
    else:
        from tortoise.projection import _prewipe_db_path_identity
        backend = _prewipe_db_path_identity(path)
    return (getattr(proj, "graph_name", "tortoise"), backend, namespace)


def build_snapshot(proj) -> dict | None:
    """Lean corpus projection + cached document vectors. None if too big."""
    g = proj.g
    try:
        count = g.query(
            "MATCH (n:Point) "
            "WHERE (n.is_operator = false OR n.is_operator IS NULL) "
            "RETURN count(n)",
        ).result_set[0][0]
        if int(count) > MAX_CORPUS_POINTS:
            logger.info(
                "Fallback snapshot skipped — corpus %d > cap %d",
                int(count), MAX_CORPUS_POINTS,
            )
            return None
        rows = g.query(_SNAPSHOT_QUERY).result_set
        points = [
            {"id": r[0], "content": r[1] or "", "pointKind": r[2] or "",
             "status": r[3] or "", "outdated": bool(r[4]),
             # R2 (#1541) D4: search_keys joins the snapshot (flat string in
             # the graph since R2; legacy lists handled by index_text).
             "search_keys": r[5] or "",
             # A5 (#2070): stored evidence mark rides the snapshot hits —
             # wired through the degraded lane (snapshot graph carries the
             # mark when the extractor wrote it; product graphs: zero marks).
             "has_answer": bool(r[6])}
            for r in rows
        ]
    except Exception as e:  # noqa: BLE001, RUF100
        logger.warning("Fallback snapshot build query failed: %s", e)
        return None

    vectorizer = doc_vecs = None
    model_id = None
    # R2 (#1541) D4: the corpus text is content ∪ search_keys — the
    # embedded-stack counterpart of the real backend's multi-field Point
    # FTS index (surface-11 normalization is the shared index_text builder).
    from tortoise.sparse import index_text
    texts = [index_text(p["content"], p["search_keys"]) for p in points]
    try:
        from tortoise.embeddings import EmbeddingModel
        model = EmbeddingModel.get()
        if model is not None:
            import numpy as np
            doc_vecs = np.asarray(
                model.encode(texts, show_progress_bar=False), dtype=np.float64,
            )
            model_id = getattr(model, "model_id", "embedding-model")
    except Exception as e:  # noqa: BLE001, RUF100
        logger.info("Fallback snapshot model encoding unavailable: %s", e)

    if doc_vecs is None:
        try:
            from tortoise.heavy_imports import import_tfidf_vectorizer  # #5718
            TfidfVectorizer = import_tfidf_vectorizer()
            tv = TfidfVectorizer()
            # Keep the sparse matrix — densify only the served slice (P2: the
            # dense 50k × vocab array is an OOM risk; csr stays lean).
            doc_vecs = tv.fit_transform(texts)
            vectorizer = tv
        except Exception as e:  # noqa: BLE001, RUF100
            logger.info("Fallback snapshot vectorization unavailable (sklearn): %s", e)

    return {
        "built_at": time.monotonic(),
        "dirty": False,
        "points": points,
        "vectorizer": vectorizer,
        "doc_vecs": doc_vecs,
        "model_id": model_id,
    }


def _encode_query(query: str, snap: dict):
    """Encode the query the way the corpus was encoded (model or sklearn)."""
    if snap.get("model_id"):
        from tortoise.embeddings import EmbeddingModel
        model = EmbeddingModel.get()
        if model is not None:
            import numpy as np
            return np.asarray(model.encode([query], show_progress_bar=False),
                              dtype=np.float64)[0]
        return None
    if snap.get("vectorizer") is not None:
        import numpy as np
        return np.asarray(snap["vectorizer"].transform([query]).toarray()[0],
                          dtype=np.float64)
    return None


def search_snapshot(
    query: str,
    snap: dict,
    *,
    limit: int = 10,
    kind: str | None = None,
    exclude_status: list[str] | None = None,
    exclude_turn_echo_session: str | None = None,
    include_terminal: bool = False,
    threshold: float = 0.0,
) -> list[dict]:
    """Score the snapshot corpus against ``query``.

    Returns the SAME shape as ``search_engine.fallback_tfidf`` (a list of
    SearchResult.to_dict() with match_source="tfidf"). Mirrors the legacy
    fallback semantics: ``kind`` (pointKind equality), terminal-status
    exclusion unless ``include_terminal`` (#1391), and ``exclude_status``
    compose. ``exclude_turn_echo_session`` (#4509) drops that session's own
    turn echoes from the CORPUS before ranking — the same pre-truncation
    contract the primary path applies, so the degraded tier cannot leak a
    capture's transcript as memory priors. When no cached vectors exist,
    delegates to the legacy scorer.
    """
    import numpy as np  # noqa: I001

    from tortoise.embeddings import search_points
    from tortoise.search_engine import (
        SearchResult, SearchScores, TERMINAL_EXCLUDED_STATUSES,
    )

    points = snap["points"]
    mask = None

    def _filter(pred) -> None:
        nonlocal points, mask
        idx = [i for i, p in enumerate(points) if pred(p)]
        points = [points[i] for i in idx]
        mask = idx if mask is None else [mask[i] for i in idx]

    if kind:
        _filter(lambda p: p["pointKind"] == kind)
    if not include_terminal:
        # #1391 two-mode semantics: terminal statuses AND the legacy outdated
        # flag are excluded unless include_terminal surfaces them.
        _term = set(TERMINAL_EXCLUDED_STATUSES)
        _filter(lambda p: p["status"] not in _term and not p.get("outdated"))
    if exclude_status:
        _ex = set(exclude_status)
        _filter(lambda p: p["status"] not in _ex)
    if exclude_turn_echo_session:
        # #4509: pre-RANKING/pre-truncation, mirroring the primary path's seam.
        # Lazy import keeps this stdlib-light leaf's import graph unchanged on
        # the default path (the branch is not entered when not opted in).
        from tortoise.retrieval import is_turn_echo_row
        _echo_sid = exclude_turn_echo_session
        _filter(lambda p: not is_turn_echo_row(
            _echo_sid,
            {"id": p.get("id"), "point_kind": p.get("pointKind"),
             "content": p.get("content")}))
    if not points:
        return []

    try:
        query_vec = _encode_query(query, snap)
    except Exception:  # noqa: BLE001, RUF100
        query_vec = None
    scored: list[dict] = []
    vectors_failed = False
    if query_vec is not None and snap["doc_vecs"] is not None:
        try:
            doc_vecs = snap["doc_vecs"]
            if mask is not None:
                doc_vecs = doc_vecs[mask] if hasattr(doc_vecs, "__getitem__") else doc_vecs
            # csr slice stays sparse; densify only what we serve
            if hasattr(doc_vecs, "toarray"):
                dense = np.asarray(doc_vecs.toarray(), dtype=np.float64)
            else:
                dense = np.asarray(doc_vecs, dtype=np.float64)
            # true cosine (query normalized too — P2 parity of similarity values)
            q_norm = np.linalg.norm(query_vec) or 1.0
            norms = np.linalg.norm(dense, axis=1, keepdims=True)
            norms[norms == 0] = 1
            sims = (dense @ query_vec) / (norms[:, 0] * q_norm)
            order = np.argsort(-sims)
            scored = [
                {"id": points[i]["id"], "content": points[i]["content"],
                 "similarity": float(sims[i])}
                for i in order
                if sims[i] >= threshold
            ][:limit]
        except Exception:  # noqa: BLE001, RUF100
            vectors_failed = True
    if (not scored and query_vec is None) or vectors_failed:
        # Cached vectors unavailable/failed — legacy in-memory scorer (same inputs).
        # R2 (#1541) D4: the scorer sees the indexed text (content ∪
        # search_keys) — same union the real stack indexes; the returned
        # payload keeps the REAL content (alias text is index-only).
        from tortoise.sparse import index_text
        real_content = {p["id"]: p["content"] for p in points}
        indexed = [
            {**p, "content": index_text(p["content"], p.get("search_keys"))}
            for p in points
        ]
        scored = search_points(query, indexed, threshold=threshold, limit=limit)
        for r in scored:
            r["content"] = real_content.get(r["id"], r["content"])

    meta = {p["id"]: p for p in points}
    return [
        SearchResult(
            id=r["id"],
            content=r["content"],
            point_kind=meta.get(r["id"], {}).get("pointKind", ""),
            scores=SearchScores(fts=None, vector=None, structural=None, rrf=r["similarity"]),
            match_source="tfidf",
            ep=None,
            # A5 (#2070): stored evidence mark rides the snapshot hits
            # (snapshot points carry has_answer when the graph wrote it).
            has_answer=bool(meta.get(r["id"], {}).get("has_answer")),
            # ⛔ No ``source_ref``/``captured_at`` here — the lean snapshot
            # projection excludes them by design, so
            # TORTOISE_SEARCH_PROVENANCE is a no-op on this tier; see the KNOWN
            # LIMITATION note on ``search_engine.search_provenance_enabled``.
        ).to_dict()
        for r in scored
    ]
