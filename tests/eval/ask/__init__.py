"""Sealed ask-lane benchmark corpus (epic #2281 D4 → W2-b, issue #9097).

The SEALED sibling of `tools/ask_spotcheck.py`'s internal distilled leg: the
committed corpus, the SEALED gold (the grading key the runner never reads) and
the `_manifest.json` fixture/gold hash the pre-registration pins.

This tree is deliberately SEPARATE from `tests/eval/write_path/`: that tree's
`corpus.py` derives its on-disk set from `fixtures/*.json` + `gold/*.gold.json`
and fails its own integrity gate on any `extra` file, so an ask fixture planted
there would break write-path's committed baselines (`corpus.py:53-54,71,175`).

Hermetic (pure stdlib — no DB, no network, no LLM).
"""
