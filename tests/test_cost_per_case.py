"""What each case cost, printed where the case is (notes/82).

The bias here is the total that hides its parts. A suite line that says
"$0.0412" is true and tells nobody which case spent it, and the dear one
is usually the case worth opening. So the tests pin three things: every
priced case line carries its own figure, the dearest is named once when
there is a choice, and neither appears where there is no real dollar to
show -- a free run and a single case both stay quiet.
"""

from __future__ import annotations

from conftest import ScriptedProvider, assistant_text
from yantra.eval_suite import CASES, SUITE_DIR
from yantra.package import MANIFEST
from yantra.pricing import ModelPrice
from yantra.types import Usage

TWO = ('[[case]]\nid = "cheap"\nuser_message = "hi"\n\n'
       '[[case]]\nid = "dear"\nuser_message = "hi"\n')


def _suite(tmp_path, cases):
    root = tmp_path / "pkg"
    (root / SUITE_DIR).mkdir(parents=True)
    (root / MANIFEST).write_text('[agent]\nname = "pkg"\n')
    (root / SUITE_DIR / CASES).write_text(cases)
    return root


def _run(monkeypatch, argv, usages):
    import yantra.cli.main as cli_main
    provider = ScriptedProvider([assistant_text("done", usage=u)
                                 for u in usages])
    monkeypatch.setattr(cli_main, "guess_provider", lambda: "anthropic")
    monkeypatch.setattr(cli_main, "load_settings", lambda name: object())
    monkeypatch.setattr(cli_main, "get_provider", lambda *a, **k: provider)
    return cli_main.main(argv)


def _priced(monkeypatch):
    import yantra.pricing as pricing
    monkeypatch.setitem(pricing._EXACT, "priced-model",
                        ModelPrice(input_per_mtok=3.0, output_per_mtok=15.0))


class TestEachCaseSaysWhatItCost:
    def test_every_priced_case_line_carries_its_own_figure(
            self, tmp_path, monkeypatch, capsys):
        _priced(monkeypatch)
        root = _suite(tmp_path, TWO)
        # 1000 in + 500 out = $0.0105; ten times that = $0.1050
        _run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                           "anthropic", "--model", "priced-model"],
             [Usage(1000, 500), Usage(10000, 5000)])
        lines = capsys.readouterr().out.splitlines()
        cheap = next(line for line in lines if "cheap" in line and "PASS" in line)
        dear = next(line for line in lines if "dear" in line and "PASS" in line)
        assert cheap.rstrip().endswith("$0.0105")
        assert dear.rstrip().endswith("$0.1050")

    def test_the_dearest_case_is_named_with_its_share(
            self, tmp_path, monkeypatch, capsys):
        _priced(monkeypatch)
        root = _suite(tmp_path, TWO)
        _run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                           "anthropic", "--model", "priced-model"],
             [Usage(1000, 500), Usage(10000, 5000)])
        out = " ".join(capsys.readouterr().out.split())
        assert "dearest case: dear · $0.1050 · 91% of what" in out

    def test_one_case_is_not_a_choice_so_nothing_is_named(
            self, tmp_path, monkeypatch, capsys):
        _priced(monkeypatch)
        root = _suite(tmp_path, '[[case]]\nid = "x"\nuser_message = "hi"\n')
        _run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                           "anthropic", "--model", "priced-model"],
             [Usage(1000, 500)])
        out = capsys.readouterr().out
        assert "$0.0105" in out and "dearest" not in out

    def test_a_free_run_prints_no_figure_and_no_dearest(
            self, tmp_path, monkeypatch, capsys):
        """A local model bills nothing; $0.0000 under every case would read
        as a broken meter, and 'dearest of nothing' as a joke."""
        root = _suite(tmp_path, TWO)
        _run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                           "ollama", "--model", "qwen3.8:27b"],
             [Usage(1000, 500), Usage(10000, 5000)])
        out = capsys.readouterr().out
        assert "$" not in out and "dearest" not in out
