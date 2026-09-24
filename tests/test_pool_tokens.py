"""Tokens per run, pooled beside the dollars.

The bias here is a pool that cannot tell the agent from the vendor. The
dollars a case cost move for two reasons -- the agent did more work, or
the price changed -- and a pool that only adds up dollars prints the same
line for both. On a local model it prints nothing at all. The tests pin:

* tokens are PER RUN, oldest report against newest, like the dollars;
* a run that counted no tokens is an unknown, not a zero;
* a move inside run-to-run jitter (10%) is not a move;
* a moved price over a flat token count says "about the same tokens",
  which is the line that puts the change on the vendor;
* a free road, with no dollars to show, still gets the token line and the
  "heaviest move" summary;
* the pool file carries both ends.
"""

from __future__ import annotations

import json

from yantra.cli.main import main
from yantra.eval_report import (CaseRecord, PriceRecord, SuiteRun, pool,
                                write_pool, write_report)


def run(case_id, tokens, *, usd=None, attempts=1,
        at="2026-09-01T00:00:00Z", rates=None):
    return SuiteRun(
        suite="pkg 0.1", provider="p", model="m", at=at, repeat=attempts,
        cases_in_suite=1, pricing=rates,
        cases=[CaseRecord(id=case_id, passed=True, attempts=attempts,
                          passes=attempts, min_pass_rate=1.0, tokens=tokens,
                          seconds=1.0, ran_model=True, usd=usd)])


LATER = "2026-09-20T00:00:00Z"


def pooled_out(tmp_path, capsys, *runs) -> str:
    paths = []
    for n, r in enumerate(runs):
        path = tmp_path / f"{n}.json"
        write_report(path, r)
        paths.append(str(path))
    assert main(["--reports", *paths, "--pool"]) == 0
    return " ".join(capsys.readouterr().out.split())


class TestTokensArePooledPerRun:
    def test_ten_repeats_and_three_are_divided_before_they_are_compared(self):
        (group,) = pool([run("x", 10_000, attempts=10),
                         run("x", 6_000, attempts=3, at=LATER)])
        (c,) = group.cases
        assert c.tokens_first == 1_000
        assert c.tokens_last == 2_000
        assert c.tokens_growth == 2.0

    def test_a_run_that_counted_no_tokens_is_not_a_zero_to_grow_from(self):
        (group,) = pool([run("x", 0), run("x", 900, at=LATER)])
        (c,) = group.cases
        assert c.tokens_first == 900          # the only report that counted
        assert c.tokens_growth == 1.0

    def test_no_report_counted_any_leaves_no_figure(self):
        (group,) = pool([run("x", 0)])
        assert group.cases[0].tokens_first is None
        assert group.cases[0].tokens_growth is None


class TestThePrintedLine:
    def test_a_free_road_gets_the_token_line_and_no_dollars(
            self, tmp_path, capsys):
        out = pooled_out(tmp_path, capsys, run("x", 1_200, usd=0.0),
                         run("x", 3_000, usd=0.0, at=LATER))
        assert "1,200 → 3,000 tokens per run (x2.5)" in out
        assert "$" not in out
        assert "heaviest move: x uses x2.5 the tokens per run" in out

    def test_a_moved_price_over_flat_tokens_names_the_tokens(
            self, tmp_path, capsys):
        a = PriceRecord(source="t", input=3.0, output=15.0)
        b = PriceRecord(source="t", input=6.0, output=15.0)
        out = pooled_out(tmp_path, capsys,
                         run("x", 1_000, usd=0.01, rates=a),
                         run("x", 1_000, usd=0.02, rates=b, at=LATER))
        assert ("$0.0100 → $0.0200 per run (x2.0) · about the same tokens "
                "per run (~1,000) -- the rates moved") in out
        assert "heaviest move" not in out

    def test_flat_tokens_and_flat_dollars_print_nothing_new(
            self, tmp_path, capsys):
        out = pooled_out(tmp_path, capsys, run("x", 1_000, usd=0.0),
                         run("x", 1_000, usd=0.0, at=LATER))
        assert "tokens per run" not in out

    def test_run_to_run_jitter_is_not_a_move(self, tmp_path, capsys):
        """Measured on the researcher suite: two green runs a minute apart
        differ by a few percent. That is not a case getting heavier."""
        out = pooled_out(tmp_path, capsys, run("x", 9_022, usd=0.0),
                         run("x", 9_103, usd=0.0, at=LATER))
        assert "tokens" not in out
        assert "heaviest move" not in out

    def test_a_case_that_got_lighter_says_so(self, tmp_path, capsys):
        out = pooled_out(tmp_path, capsys, run("x", 25_653),
                         run("x", 8_956, at=LATER))
        assert "25,653 → 8,956 tokens per run (x0.3)" in out
        assert "heaviest move" not in out

    def test_one_report_has_no_token_movement_to_show(self, tmp_path, capsys):
        out = pooled_out(tmp_path, capsys, run("x", 1_000))
        assert "tokens" not in out

    def test_the_heaviest_line_is_dropped_when_it_repeats_the_dearest(
            self, tmp_path, capsys):
        out = pooled_out(tmp_path, capsys, run("x", 1_000, usd=0.01),
                         run("x", 4_000, usd=0.04, at=LATER))
        assert "dearest move: x costs x4.0" in out
        assert "heaviest move" not in out

    def test_a_price_cut_that_hides_a_hungrier_case_is_still_named(
            self, tmp_path, capsys):
        """Dollars flat, tokens doubled: the dollar line alone says nothing
        changed, and the agent is doing twice the work."""
        out = pooled_out(tmp_path, capsys, run("x", 1_000, usd=0.01),
                         run("x", 2_000, usd=0.01, at=LATER))
        assert "1,000 → 2,000 tokens per run (x2.0)" in out
        assert "heaviest move: x uses x2.0" in out


class TestThePoolFile:
    def test_both_ends_are_written_rounded(self, tmp_path):
        path = tmp_path / "pool.json"
        write_pool(path, pool([run("x", 1_000, attempts=3),
                               run("x", 900, at=LATER)]))
        (c,) = json.loads(path.read_text())["pools"][0]["cases"]
        assert c["tokens_first"] == 333.3
        assert c["tokens_last"] == 900
