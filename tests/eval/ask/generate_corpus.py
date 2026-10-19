"""Generate the sealed ask corpus (fixtures + SEALED gold + `_manifest.json`).

Run once and commit the output:

```
.venv/bin/python -m tests.eval.ask.generate_corpus
```

The corpus is HAND-AUTHORED, hermetic and small on purpose — six cases over
three planted knowledge-update pairs (residence / diet / employer), each pair
contributing:

* a ``knowledge-update`` case — "what is true NOW?" (epic #2281's D7 class);
* a ``verbatim`` case — "what did I actually SAY on <old date>?" — the
  no-regression control. The distilled corpus is seeded into the same store
  for this arm, so the control measures the thing the exit bar names: adding
  distilled claims and a supersession must not make the reader worse at
  recalling the verbatim/episodic record. A supersession that rewrites history
  is a regression even when the knowledge-update number improves.

The answer keys are written to `gold/` (SEALED — the runner never reads them)
and the fixtures to `fixtures/` (no grading key, asserted by
`corpus.fixture_issues`).
"""
from __future__ import annotations

from pathlib import Path

from tests.eval.ask import corpus

#: The authored source of truth. Every field is hand-written; nothing here is
#: generated from a dataset, so every row is reviewable on its own.
PAIRS: tuple[dict, ...] = (
    {
        "case_id": "residence",
        "old": {"text": "I live in Lisbon.", "session_id": "residence-old",
                "valid_from": "2023-03-02", "date": "2023/03/02 (Thu) 10:00"},
        "new": {"text": "I live in Porto.", "session_id": "residence-new",
                "valid_from": "2023-05-20", "date": "2023/05/20 (Sat) 10:00"},
        "ku_question": "Where do I live?",
        "verbatim_question": (
            "What did I say about where I live in my message from 2023/03/02?"),
    },
    {
        "case_id": "diet",
        "old": {"text": "I eat a vegetarian diet.", "session_id": "diet-old",
                "valid_from": "2023-04-04", "date": "2023/04/04 (Tue) 08:30"},
        "new": {"text": "I eat a vegan diet.", "session_id": "diet-new",
                "valid_from": "2023-05-26", "date": "2023/05/26 (Fri) 08:30"},
        "ku_question": "What diet am I on?",
        "verbatim_question": (
            "What did I say about my diet in my message from 2023/04/04?"),
    },
    {
        "case_id": "employer",
        "old": {"text": "I work at Contoso Ltd.", "session_id": "employer-old",
                "valid_from": "2023-01-09", "date": "2023/01/09 (Mon) 09:15"},
        "new": {"text": "I work at Northwind Traders.",
                "session_id": "employer-new",
                "valid_from": "2023-05-15", "date": "2023/05/15 (Mon) 09:15"},
        "ku_question": "Who do I work for?",
        "verbatim_question": (
            "Who did I say I worked for in my message from 2023/01/09?"),
    },
)

#: The run asks its questions "now", after both sessions.
QUESTION_DATE = "2023/06/01 (Thu) 10:00"

#: Judge prompt type per class (LongMemEval's own vocabulary — the same
#: templates the 21-question spot-check grades through).
QUESTION_TYPE_OF_CLASS = {
    corpus.CLASS_KNOWLEDGE_UPDATE: "knowledge-update",
    corpus.CLASS_VERBATIM: "single-session-user",
}


def _fixture(pair: dict, case_id: str, case_class: str, question: str) -> dict:
    old, new = pair["old"], pair["new"]
    return {
        "case_id": case_id,
        "case_class": case_class,
        "question": question,
        "question_date": QUESTION_DATE,
        "question_type": QUESTION_TYPE_OF_CLASS[case_class],
        "old_claim": {"text": old["text"], "session_id": old["session_id"],
                      "valid_from": old["valid_from"]},
        "new_claim": {"text": new["text"], "session_id": new["session_id"],
                      "valid_from": new["valid_from"]},
        "haystack_session_ids": [old["session_id"], new["session_id"]],
        "haystack_dates": [old["date"], new["date"]],
        "haystack_sessions": [
            [{"role": "user", "content": old["text"]}],
            [{"role": "user", "content": new["text"]}],
        ],
    }


def _gold(case_id: str, case_class: str, answer: str,
          superseded: str) -> dict:
    return {
        "case_id": case_id,
        "case_class": case_class,
        # The SEALED grading key: what a correct answer must say.
        "answer": answer,
        # The claim the D8 check watches (never shown to the reader as gold —
        # the D8 check reads the READER EVIDENCE, not the key).
        "superseded_claim": superseded,
    }


def build() -> tuple[dict[str, dict], dict[str, dict]]:
    """`(fixtures, gold)` keyed by case id — the exact bytes to commit."""
    fixtures: dict[str, dict] = {}
    gold: dict[str, dict] = {}
    for pair in PAIRS:
        base = pair["case_id"]
        fixtures[base] = _fixture(pair, base, corpus.CLASS_KNOWLEDGE_UPDATE,
                                  pair["ku_question"])
        gold[base] = _gold(base, corpus.CLASS_KNOWLEDGE_UPDATE,
                           pair["new"]["text"], pair["old"]["text"])
        verbatim = f"{base}-verbatim"
        fixtures[verbatim] = _fixture(pair, verbatim, corpus.CLASS_VERBATIM,
                                      pair["verbatim_question"])
        # The verbatim control's key is the OLD turn's words: the question asks
        # what was said then, so the current state is the WRONG answer here.
        gold[verbatim] = _gold(verbatim, corpus.CLASS_VERBATIM,
                               pair["old"]["text"], pair["old"]["text"])
    return fixtures, gold


def write(root: Path = corpus.ASK_DIR) -> dict:
    fixtures, gold = build()
    issues: list[str] = []
    for fixture in fixtures.values():
        issues.extend(corpus.fixture_issues(fixture))
    for key in gold.values():
        issues.extend(corpus.gold_issues(key))
    if issues:
        raise SystemExit("refusing to write a malformed corpus:\n  "
                         + "\n  ".join(issues))
    for case_id, fixture in fixtures.items():
        corpus.write_json(corpus.fixture_path(case_id, root), fixture)
    for case_id, key in gold.items():
        corpus.write_json(corpus.gold_path(case_id, root), key)
    files = {
        corpus._relative(p, root): corpus._sha256_file(p)
        for p in corpus.corpus_file_paths(root)
    }
    manifest = {
        "corpus": "ask",
        "schema_version": 1,
        "generator": "tests/eval/ask/generate_corpus.py",
        "classes": list(corpus.CASE_CLASSES),
        "case_ids": sorted(fixtures),
        "files": files,
        "fixtures_hash": corpus.compute_fixtures_hash(root),
    }
    corpus.write_json(root / "_manifest.json", manifest)
    return manifest


def main() -> int:
    manifest = write()
    print(f"wrote {len(manifest['files'])} corpus files "
          f"({len(manifest['case_ids'])} cases) — "
          f"fixtures_hash {manifest['fixtures_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
