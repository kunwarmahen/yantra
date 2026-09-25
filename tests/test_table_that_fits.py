"""The side-by-side table, when it has totals, an order, and ten columns
(notes/83).

The bias here is a table that looks fine at three columns and quietly
lies at ten. Three specific ways:

* **A sort that hides.** "Show me the cases that disagree" is a filter in
  most tools, and a filter drops exactly the rows a comparison exists to
  show (notes/49). So every sort test asserts the row COUNT survives.
* **Totals that are not under their columns.** A footer padded by
  different arithmetic from the cells puts one run's sum under another
  run's header. The tests read columns by position.
* **Wrapping.** A terminal that wraps a wide row puts its second half
  under the wrong header, and nothing errors. The tests render at a fixed
  width and assert no line is wider than it.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from yantra.cli.main import _column_blocks, _render_matrix, main
from yantra.eval_report import (CaseRecord, SuiteRun, line_up, write_report)


def case(id, passed=True):
    return CaseRecord(id=id, passed=passed, attempts=1,
                      passes=1 if passed else 0, min_pass_rate=1.0,
                      tokens=100, seconds=1.0, ran_model=True)


def run(*cases, usd=None):
    if usd is not None:
        cases = [CaseRecord(**{**{f: getattr(c, f) for f in c.__slots__},
                               "usd": usd}) for c in cases]
    return SuiteRun(suite="pkg 1.0", provider="ollama", model="m",
                    at="2026-01-01T00:00:00Z", repeat=1, cases=list(cases),
                    cases_in_suite=len(cases))


def render(runs, labels, width=120, sort=None) -> list[str]:
    buf = io.StringIO()
    console = Console(file=buf, width=width, force_terminal=False)
    _render_matrix(console, line_up(runs), labels, sort=sort)
    return buf.getvalue().splitlines()


def rows_of(lines, ids):
    return [line.split()[0] for line in lines
            if line.strip() and line.split()[0] in ids]


THREE = [run(case("agree"), case("split"), case("red-twice", False)),
         run(case("agree"), case("split", False), case("red-twice", False)),
         run(case("agree"), case("split"), case("red-twice"))]
IDS = {"agree", "split", "red-twice"}


class TestTheSort:
    def test_disagree_puts_the_split_rows_first_and_drops_none(self):
        order = rows_of(render(THREE, ["a", "b", "c"], sort="disagree"), IDS)
        assert order == ["split", "red-twice", "agree"]

    def test_red_puts_the_most_red_cells_first(self):
        order = rows_of(render(THREE, ["a", "b", "c"], sort="red"), IDS)
        assert order == ["red-twice", "split", "agree"]

    def test_id_is_alphabetical(self):
        order = rows_of(render(THREE, ["a", "b", "c"], sort="id"), IDS)
        assert order == ["agree", "red-twice", "split"]

    def test_no_sort_is_the_last_runs_order(self):
        order = rows_of(render(THREE, ["a", "b", "c"]), IDS)
        assert order == ["agree", "split", "red-twice"]

    def test_a_blank_is_not_a_vote_against(self):
        """A case one run never had is a hole, not a disagreement."""
        table = line_up([run(case("x")), run(case("y")),
                         run(case("x"), case("y"))])
        assert not table.disagrees("x") and not table.disagrees("y")

    def test_an_unknown_sort_is_refused_by_name(self):
        with pytest.raises(ValueError, match="known: disagree, red, id"):
            line_up(THREE).sorted_by("cost")


class TestTheFooter:
    def test_totals_sit_under_their_own_columns(self):
        runs = [run(case("x"), case("y")), run(case("x"), case("y", False)),
                run(case("x", False), case("y", False))]
        lines = render(runs, ["first", "second", "third"])
        passed = next(line for line in lines if line.strip().startswith("passed"))
        assert passed.split()[1:] == ["2/2", "1/2", "0/2"]
        header = next(line for line in lines if line.strip().startswith("case"))
        # right-aligned under each label: every total ends where its label does
        for label, total in zip(["first", "second", "third"],
                                ["2/2", "1/2", "0/2"], strict=True):
            assert header.index(label) + len(label) == \
                passed.index(total) + len(total)

    def test_cost_row_only_when_some_run_cost_money(self):
        free = render([run(case("x"))] * 3, ["a", "b", "c"])
        assert not any(line.strip().startswith("cost") for line in free)
        paid = render([run(case("x"), usd=0.01)] * 3, ["a", "b", "c"])
        cost = next(line for line in paid if line.strip().startswith("cost"))
        assert cost.split()[1:] == ["$0.0100"] * 3

    def test_beside_a_priced_run_zero_is_free_and_unknown_is_a_dash(self):
        """A local run records a real 0.0; printing it as $0.0000 reads as
        a broken meter, and printing nothing would read as unknown."""
        lines = render([run(case("x"), usd=0.01), run(case("x"), usd=0.0),
                        run(case("x"))], ["a", "b", "c"])
        cost = next(line for line in lines if line.strip().startswith("cost"))
        assert cost.split()[1:] == ["$0.0100", "free", "--"]


class TestAWideTable:
    LABELS = [f"model-{n:02d}" for n in range(10)]

    def test_ten_columns_split_into_blocks_that_fit(self):
        runs = [run(case("a-case-with-a-long-name"), case("short"))] * 10
        lines = render(runs, self.LABELS, width=60)
        assert all(len(line) <= 60 for line in lines), \
            max(lines, key=len)
        prose = " ".join(" ".join(lines).split())      # undo rich's wrap
        assert "shown in 4 blocks" in prose
        # every column appears in exactly one header
        headers = [line for line in lines if line.strip().startswith("case")]
        shown = [w for h in headers for w in h.split()[1:]]
        assert shown == self.LABELS
        # and every block repeats every row
        assert sum(1 for line in lines
                   if line.strip().startswith("short")) == len(headers)

    def test_a_table_that_fits_is_one_block(self):
        lines = render([run(case("x"))] * 3, ["a", "b", "c"], width=120)
        assert not any("blocks" in line for line in lines)

    def test_every_block_holds_at_least_one_column(self):
        """A column wider than the screen still has to be shown somewhere."""
        assert _column_blocks([50, 50, 50], room=10) == [[0], [1], [2]]
        assert _column_blocks([3, 3, 3], room=8) == [[0, 1], [2]]


class TestTheFlag:
    def test_sort_on_three_reports_works(self, tmp_path, capsys):
        paths = []
        for n, r in zip("xyz", THREE, strict=True):
            path = tmp_path / f"{n}.json"
            write_report(path, r)
            paths.append(str(path))
        assert main(["--reports", *paths, "--sort", "disagree"]) == 0
        assert "sorted: 2 case(s) the runs disagree on" in " ".join(
            capsys.readouterr().out.split())

    @pytest.mark.parametrize("extra", [[], ["--pool"]])
    def test_sort_without_a_table_is_refused(self, tmp_path, capsys, extra):
        """Two reports are a difference and a pool is a list; a sort that
        did nothing would read as one that had worked."""
        paths = []
        for n in "xy":
            path = tmp_path / f"{n}.json"
            write_report(path, run(case("x")))
            paths.append(str(path))
        assert main(["--reports", *paths, "--sort", "red", *extra]) == 2
        assert "--sort orders the rows of a table" in capsys.readouterr().err
