"""How many names does a local model miss? The measurement notes/86 asked for.

`--trace-redact-words` (notes/86) scrubs the names an operator LISTS. The
obvious next step is a model as a second reader: ask it for the names in
each tool result and scrub those too, so a customer nobody listed does not
reach the file. Whether that is safe is a question about the model, not
about code, and notes/86 set the bar before anything was built: if it
misses more than a few names in a hundred, the flag gives false comfort.
notes/89 has what it found.

So this reads a labelled set -- texts shaped like what tools return
(handover notes, git logs, tickets, CSV rows, email, minutes, chat, JSON)
with every person's name marked -- asks the model for the names in each,
and scores the answer THE WAY THE SCRUBBER WOULD USE IT. What the model
returns is compiled with the same matcher as `--trace-redact-words`, and a
labelled name counts as caught only when every letter of it would have
been scrubbed. "Ana" returned for "Ana Lima" is a miss: "Lima" would still
be in the file.

Two other numbers ride along:

    over-scrubbed  -- phrases the model's list would have hidden that are
                      not names ("Acme Inc.", "grace" in "grace period")
    failed         -- calls that errored or answered nothing usable; the
                      design writes no contents at all when that happens,
                      so a failure costs evidence, not privacy

It scores the answer twice: as the model returned it, and with every word
of a multi-word name added, which is what the recorder scrubs
(yantra/name_reader.py). The first number is the model; the second is
the feature.

    uv run python examples/name_recall_trial.py \\
        --provider ollama --model qwen3.8:latest --out qwen.jsonl

`--rescore qwen.jsonl` scores a saved run again without calling the model.
Swap in your own labelled set with `--cases`: one JSON object a line,
`{"text": ..., "names": [...]}`, where names lists every way a person is
written in that text ("Ana Lima", and "Ana" if she is also called that).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from yantra import get_provider, load_settings
from yantra.name_reader import ReaderFailed, read_names, with_each_word
from yantra.trace import _word_pattern

CASES = Path(__file__).resolve().parent / "name_recall_cases.jsonl"

def ask(provider, model: str, text: str) -> tuple[list[str] | None, float]:
    """The model's names for one text -- exactly as the recorder asks
    (yantra/name_reader.py) -- or None when the call failed."""
    started = time.time()
    try:
        names = read_names(provider, model, text)
    except ReaderFailed as exc:
        print(f"  call failed: {exc}", file=sys.stderr)
        return None, time.time() - started
    return names, time.time() - started


def spans(pattern: re.Pattern[str] | None, text: str) -> list[tuple[int, int]]:
    return [m.span() for m in pattern.finditer(text)] if pattern else []


def compile_words(words: list[str]) -> re.Pattern[str] | None:
    words = [" ".join(w.split()) for w in words if len(w.strip()) >= 2]
    return re.compile(_word_pattern(words)) if words else None


def score(case: dict, found: list[str] | None) -> dict:
    """Which labelled names the found list would have scrubbed, and what
    else it would have scrubbed along the way."""
    text = case["text"]
    wanted = spans(compile_words(case["names"]), text)
    if found is None:
        return {"names": len(wanted), "caught": 0, "missed": [],
                "over": [], "failed": True}
    covered = set()
    hits = spans(compile_words(found), text)
    for start, end in hits:
        covered.update(range(start, end))
    missed = [text[s:e] for s, e in wanted
              if any(text[i].isalnum() and i not in covered
                     for i in range(s, e))]
    inside = set()
    for start, end in wanted:
        inside.update(range(start, end))
    over = [text[s:e] for s, e in hits
            if not any(i in inside for i in range(s, e))]
    return {"names": len(wanted), "caught": len(wanted) - len(missed),
            "missed": missed, "over": over, "failed": False}


def report(rows: list[dict]) -> None:
    by_kind: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_kind[row["kind"]].append(row)
    print(f"\n{'kind':<20} {'texts':>5} {'names':>6} {'missed':>6} "
          f"{'over':>5} {'failed':>6}")
    for kind, group in by_kind.items():
        print(f"{kind:<20} {len(group):>5} "
              f"{sum(r['names'] for r in group):>6} "
              f"{sum(len(r['missed']) for r in group if not r['failed']):>6} "
              f"{sum(len(r['over']) for r in group):>5} "
              f"{sum(r['failed'] for r in group):>6}")
    scored = [r for r in rows if not r["failed"]]
    names = sum(r["names"] for r in scored)
    missed = sum(len(r["missed"]) for r in scored)
    over = sum(len(r["over"]) for r in scored)
    failed = len(rows) - len(scored)
    per_hundred = 100 * missed / names if names else 0.0
    print(f"\n{len(rows)} texts, {names} names in the texts that answered: "
          f"{missed} missed ({per_hundred:.1f} in a hundred), "
          f"{over} phrase(s) over-scrubbed, {failed} call(s) failed")
    for row in scored:
        for name in row["missed"]:
            print(f"  missed {name!r:<24} ({row['kind']})")
    for row in scored:
        for phrase in row["over"]:
            print(f"  over   {phrase!r:<24} ({row['kind']})")


def report_both(saved: list[dict]) -> None:
    """The model's list as it came back, then as the recorder uses it:
    with every word of a multi-word name added (notes/89)."""
    print("\n== as the model returned them")
    report([{**score(s, s["found"]), "kind": s.get("kind", "-")}
            for s in saved])
    print("\n== with each word of a name added, as the recorder scrubs")
    report([{**score(s, None if s["found"] is None
                     else with_each_word(s["found"])),
             "kind": s.get("kind", "-")} for s in saved])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default="ollama")
    parser.add_argument("--model", default="qwen3.8:latest")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out", type=Path,
                        help="save each answer, to --rescore later")
    parser.add_argument("--rescore", type=Path,
                        help="score a saved run instead of calling the model")
    args = parser.parse_args()

    if args.rescore:
        saved = [json.loads(line) for line in args.rescore.read_text(
            encoding="utf-8").splitlines() if line.strip()]
        report_both(saved)
        return

    cases = [json.loads(line) for line in args.cases.read_text(
        encoding="utf-8").splitlines() if line.strip()]
    provider = get_provider(args.provider, load_settings(args.provider))
    out = args.out.open("w", encoding="utf-8") if args.out else None
    rows, answered = [], []
    try:
        for n, case in enumerate(cases, 1):
            found, seconds = ask(provider, args.model, case["text"])
            row = {**score(case, found), "kind": case.get("kind", "-")}
            rows.append(row)
            print(f"{n:>3}/{len(cases)} {row['kind']:<9} {seconds:5.1f}s "
                  f"caught {row['caught']}/{row['names']}"
                  + (f"  missed {row['missed']}" if row["missed"] else ""),
                  flush=True)
            answered.append({**case, "found": found})
            if out:
                out.write(json.dumps(answered[-1], ensure_ascii=False)
                          + "\n")
                out.flush()
    finally:
        if out:
            out.close()
        provider.close()
    report_both(answered)


if __name__ == "__main__":
    main()
