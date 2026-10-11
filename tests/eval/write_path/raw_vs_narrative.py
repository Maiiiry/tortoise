"""Raw-vs-narrative falsifier — extractor v4, E5 (tortoise #7947).

Pre-registered: the corpus, split, scoring and decision rule are fixed in the
issue comment BEFORE any run. Nothing here may be tuned after a run starts.

**The question (§10's falsifier):** does the narrative lose the hedging / scope /
stance information a claim's confidence depends on?

**The two arms differ in exactly one value.** In the production pipeline
(``extractor_v2.extract_session_v2``) the S2 extraction call consumes ``story``::

    story = compile_stories(chunk_stories)
    embed_list = run_s2(model, story, master, session_date=..., edus=edus, ...)

* **arm B (narrative, as shipped):** ``story = compile_stories(chunk_stories)``
* **arm A (raw):** ``story = _edus_to_text(edus)`` — the raw transcript

Everything else is byte-identical: one pinned model, one ``run_s2`` (so one
prompt scaffolding), one chunking rule, the same ``edus``. That is what makes
the comparison mean something — if the two arms used different prompts, a
difference would say nothing about the input.

Arm B's S1 calls are REAL (the shipped ``run_s1``); they are simply not needed
for arm A, whose whole point is to reach S2 without a narrative.

Usage (keys come from the repo .env — always go through the wrapper, #2718/#4860)::

    PYTHONPATH=$PWD tools/run-with-eval-keys.sh \\
      .venv/bin/python -m tests.eval.write_path.raw_vs_narrative --sessions wp01_quarry_debug

Output: one JSON per session under ``--out`` (default ``~/.cache/pi-7947/arms``),
holding both arms' ``embed_list``, the story each arm fed to S2, and the run's
pinned parameters. Scoring is a separate step (``score_qualifiers.py``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import tortoise.extractor_v2 as ev2  # noqa: E402

WRITE_PATH_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = WRITE_PATH_DIR / "fixtures"
GOLD_DIR = WRITE_PATH_DIR / "gold"

#: Frozen for this measurement. Changing any of these invalidates a run.
MODEL_ID = "deepseek/deepseek-v4-flash"
CHUNK_SIZE = 50
SESSION_DATE = "2026-10-10"

#: The two pre-registered arms, in report order.
ARMS = ("raw", "narrative")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def build_inputs(model, conversation: list[dict], *, chunk_size: int = CHUNK_SIZE):
    """Return the S2 input for each arm, plus what produced it.

    Arm B calls the SHIPPED S1 (real generation). Arm A skips S1 entirely and
    hands S2 the raw transcript — the narrative is never produced, which is
    exactly what "extract without the narrative" means.
    """
    edus = ev2._edus_from_conversation(conversation)
    if not edus:
        return {"edus": [], "chunks": 0, "stories": {}, "errors": ["no conversation content"]}

    raw_text = ev2._edus_to_text(edus)

    chunks = ev2.chunk_transcript(edus, target=chunk_size)
    chunk_stories: list[str] = []
    errors: list[str] = []
    for chunk in chunks:
        try:
            chunk_stories.append(
                ev2.run_s1(model, ev2._edus_to_text(chunk), session_date=SESSION_DATE)
            )
        except Exception as e:  # per-chunk failure is non-fatal in production too
            errors.append(f"S1 chunk failed: {type(e).__name__}: {e}")
    narrative = ev2.compile_stories(chunk_stories) if chunk_stories else ""

    return {
        "edus": edus,
        "chunks": len(chunks),
        "chunk_stories": chunk_stories,
        "stories": {"raw": raw_text, "narrative": narrative},
        "errors": errors,
    }


def run_arms(model, conversation: list[dict], *, chunk_size: int = CHUNK_SIZE):
    """Run both arms. Returns per-arm embed lists + a common provenance block."""
    built = build_inputs(model, conversation, chunk_size=chunk_size)
    out: dict = {
        "model_id": MODEL_ID,
        "chunk_size": chunk_size,
        "session_date": SESSION_DATE,
        "chunks": built["chunks"],
        "errors": list(built["errors"]),
        "stories": built["stories"],
        "story_sha": {k: _sha(v) for k, v in built["stories"].items()},
        "story_chars": {k: len(v) for k, v in built["stories"].items()},
        "embed_list": {},
        "s2_stats": {},
        "s2_errors": {},
    }
    for arm in ARMS:
        story = built["stories"].get(arm) or ""
        if not story:
            out["embed_list"][arm] = {}
            out["s2_errors"][arm] = "empty story — the arm produced no input"
            continue
        stats: dict = {}
        t0 = time.time()
        try:
            # Byte-identical call for both arms: only ``story`` differs.
            out["embed_list"][arm] = ev2.run_s2(
                model, story, None,
                session_date=SESSION_DATE,
                edus=built["edus"],
                stats=stats,
                core_only=False,
            )
        except Exception as e:
            out["embed_list"][arm] = {}
            out["s2_errors"][arm] = f"{type(e).__name__}: {e}"
        out["s2_stats"][arm] = stats
        out["s2_seconds"] = out.get("s2_seconds", {})
        out["s2_seconds"][arm] = round(time.time() - t0, 1)
    return out


def _load(session_id: str) -> list[dict]:
    p = FIXTURES_DIR / f"{session_id}.json"
    return json.loads(p.read_text())["conversation"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="raw-vs-narrative arms (#7947)")
    ap.add_argument("--sessions", nargs="+", required=True,
                    help="fixture session ids, e.g. wp01_quarry_debug")
    ap.add_argument("--out", default=str(Path.home() / ".cache" / "pi-7947" / "arms"))
    ap.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    args = ap.parse_args(argv)

    outdir = Path(args.out)
    outdir.mkdir(parents=True, exist_ok=True)

    from tortoise.sdk import _model_adapter

    # PINNED: the same adapter construction the production extractor uses.
    model = _model_adapter(MODEL_ID, max_tokens=None)
    print(f"[arms] model={MODEL_ID} chunk_size={args.chunk_size} date={SESSION_DATE}")

    for session_id in args.sessions:
        conversation = _load(session_id)
        t0 = time.time()
        res = run_arms(model, conversation, chunk_size=args.chunk_size)
        res["session_id"] = session_id
        res["turns"] = len(conversation)
        res["seconds"] = round(time.time() - t0, 1)
        dest = outdir / f"{session_id}.arms.json"
        dest.write_text(json.dumps(res, indent=2, default=str))
        print(f"[arms] {session_id}: turns={res['turns']} chunks={res['chunks']} "
              f"raw_chars={res['story_chars'].get('raw')} "
              f"narr_chars={res['story_chars'].get('narrative')} "
              f"in {res['seconds']}s -> {dest}")
        for arm in ARMS:
            err = res["s2_errors"].get(arm)
            if err:
                print(f"        !! {arm}: {err}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
