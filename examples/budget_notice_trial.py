"""Does telling the agent it is nearly out of budget change what it does?

`--budget-notice` (notes/43) tells the MODEL, once, that its turn is about
to be stopped, and asks it to finish with what it has. Whether that works
is a question about models, not about code, so this runs the same turn
many times with the notice and many times without it, and counts how each
turn ended:

    finished  -- the model gave its answer before the ceiling stopped it
    cut off   -- the loop stopped the turn (over_budget), with no answer

The warning usually lands on the last call the turn can afford, so what
decides the outcome is what the model does with THAT call: answer, or ask
for more tools (which the loop then refuses to run).

Arms alternate trial by trial, so a model server that warms up or slows
down part-way through affects both arms the same way.

A local model bills nothing, so its ceiling would never fire. Price it
yourself for the trial, in a YANTRA_PRICES file (notes/64) -- any rates
will do, since only the ceiling's position relative to the turn matters:

    echo '{"qwen3.8:latest": {"input": 1.0, "output": 5.0}}' > prices.json
    YANTRA_PRICES=prices.json uv run python examples/budget_notice_trial.py \\
        --provider ollama --model qwen3.8:latest --ceiling 0.010 --trials 10

Pick a ceiling the turn usually runs a little PAST when nobody is warned
(`--ceiling 100 --trials 1` shows what a whole turn costs). A turn that
fits easily is never warned, and a ceiling far below the turn's cost
stops it before the notice has a chance to matter.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

from yantra import load_package
from yantra.agent import BudgetWarning, ToolExecuted, TurnEnd
from yantra.permissions import allow_read_only

ROOT = Path(__file__).resolve().parent / "agents" / "researcher"

TASK = ("Read prompt.md, agent.toml, subagents/fact_checker.md, "
        "skills/source-brief/SKILL.md and sources/rate-limiting.md, then "
        "write one paragraph on what this package is for and how its parts "
        "fit together. Cite each file you used.")


def one_turn(spec, *, told: bool, task: str) -> dict:
    """One turn, and the facts about how it ended."""
    agent = spec.build(permissions=allow_read_only, cwd=spec.root)
    if agent.budget is None or not agent.budget.metered:
        sys.exit("error: the ceiling is inert for this model -- price it in "
                 "a YANTRA_PRICES file first (see the docstring)")
    agent.budget.notify_agent = told
    warned_at = None
    tools_after = 0
    tools = 0
    outcome = "?"
    answer = ""
    for event in agent.run_streaming(task):
        if isinstance(event, BudgetWarning) and warned_at is None:
            warned_at = tools
        elif isinstance(event, ToolExecuted):
            tools += 1
            if warned_at is not None:
                tools_after += 1
        elif isinstance(event, TurnEnd):
            outcome = event.reason
            if event.response is not None:
                answer = event.response.message.text().strip()
    return {"told": told, "warned": warned_at is not None,
            "outcome": outcome, "tools": tools, "tools_after": tools_after,
            "spent": agent.budget.spent, "answer_chars": len(answer)}


def summarise(rows: list[dict], told: bool) -> str:
    arm = [r for r in rows if r["told"] is told]
    warned = [r for r in arm if r["warned"]]
    finished = sum(1 for r in arm if r["outcome"] == "end_turn")
    cut = sum(1 for r in arm if r["outcome"] == "over_budget")
    after = (sum(r["tools_after"] for r in warned) / len(warned)
             if warned else 0.0)
    spent = sum(r["spent"] for r in arm) / len(arm) if arm else 0.0
    name = "told" if told else "not told"
    return (f"{name:>9}: {finished}/{len(arm)} finished · {cut} cut off · "
            f"{len(warned)} warned · {after:.1f} tool calls after the "
            f"warning · ~${spent:.4f} a turn")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", default=str(ROOT))
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--ceiling", type=float, required=True,
                        help="max_usd_per_turn for every trial")
    parser.add_argument("--trials", type=int, default=10,
                        help="turns PER ARM")
    parser.add_argument("--task", default=TASK)
    args = parser.parse_args()
    # A cloud key usually lives in .env, as it does for the CLI.
    from yantra.config import _load_dotenv
    _load_dotenv()

    spec = replace(load_package(args.agent), max_usd_per_turn=args.ceiling,
                   provider=args.provider or None, model=args.model or None)
    rows: list[dict] = []
    for trial in range(args.trials):
        for told in (trial % 2 == 0, trial % 2 != 0):   # alternate who goes first
            row = one_turn(spec, told=told, task=args.task)
            rows.append(row)
            print(f"trial {trial + 1:>2} {'told' if told else 'not told':>9}: "
                  f"{row['outcome']:<11} {'warned' if row['warned'] else '      '} "
                  f"{row['tools']} tools ({row['tools_after']} after warning) "
                  f"~${row['spent']:.4f} · answer {row['answer_chars']} chars",
                  flush=True)
    print()
    print(summarise(rows, told=False))
    print(summarise(rows, told=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
