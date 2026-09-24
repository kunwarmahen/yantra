"""Two more things that change a rate while every name stays the same.

The bias here is the same as the package fingerprint's (notes/68): a
pool that trusts a name. Note 68 caught an edited package under an
unbumped version and left two gaps (notes/72):

* **An edited case.** ``evals/`` is left out of the package fingerprint
  on purpose, so a case whose grader was loosened pooled its strict runs
  with its lenient ones. Pinned: a per-case fingerprint of the table as
  written plus the grader module, that splits THAT case only; prose in
  ``description`` does not count.
* **A re-pulled tag.** ``qwen3.8:latest`` after an ``ollama pull`` is new
  weights under an old name. Pinned: the digest Ollama reports is
  recorded, splits a pool, and is named by ``--against``; anything that
  goes wrong asking for it is unknown, never a failed run.

And the rule every fingerprint follows: unknown is not different.
"""

from __future__ import annotations

import json

import httpx

from yantra.cli.main import main
from yantra.eval_report import (CaseRecord, SuiteRun, compare, pool,
                                read_report, write_report)
from yantra.eval_suite import load_cases
from yantra.fingerprint import case_fingerprint, weights


def suite(tmp_path, cases_toml, graders=None):
    root = tmp_path / "pkg"
    (root / "evals").mkdir(parents=True, exist_ok=True)
    (root / "agent.toml").write_text('[agent]\nname = "pkg"\n')
    (root / "evals" / "cases.toml").write_text(cases_toml)
    if graders is not None:
        (root / "evals" / "graders.py").write_text(graders)
    return root


def fp_of(root, case_id="x"):
    return next(c.fingerprint for c in load_cases(root) if c.id == case_id)


CASE = '[[case]]\nid = "x"\nuser_message = "hi"\n'
CHECKED = CASE + 'check = "graders:ok"\n'
GRADER = "def ok(answer):\n    return True\n"


class TestTheCaseFingerprint:
    def test_an_edited_task_changes_it(self, tmp_path):
        before = fp_of(suite(tmp_path, CASE))
        assert fp_of(suite(tmp_path, CASE.replace("hi", "hello"))) != before

    def test_prose_in_the_description_does_not(self, tmp_path):
        before = fp_of(suite(tmp_path, CASE))
        edited = CASE + 'description = "a typo, fixed"\n'
        assert fp_of(suite(tmp_path, edited)) == before

    def test_an_edited_grader_changes_the_case_that_uses_it(self, tmp_path):
        before = fp_of(suite(tmp_path, CHECKED, GRADER))
        loosened = GRADER.replace("True", "len(answer) >= 0")
        assert fp_of(suite(tmp_path, CHECKED, loosened)) != before

    def test_and_not_a_case_that_does_not(self, tmp_path):
        both = CHECKED + '\n[[case]]\nid = "y"\nuser_message = "yo"\n'
        before = fp_of(suite(tmp_path, both, GRADER), "y")
        after = fp_of(suite(tmp_path, both, GRADER + "# edited\n"), "y")
        assert after == before

    def test_it_is_twelve_hex(self):
        assert len(case_fingerprint({"id": "x"}, None)) == 12


class TestTheWeights:
    def tags(self, monkeypatch, payload=None, error=None):
        def fake_get(url, timeout):
            assert url == "http://localhost:11434/api/tags"
            if error:
                raise error
            return httpx.Response(200, json=payload)
        monkeypatch.setattr(httpx, "get", fake_get)

    def test_the_digest_ollama_reports(self, monkeypatch):
        self.tags(monkeypatch, {"models": [
            {"name": "qwen3.8:latest", "digest": "22130167c4c20e20ffff"}]})
        assert weights("ollama", "http://localhost:11434/v1",
                       "qwen3.8") == "22130167c4c2"

    def test_a_cloud_provider_is_unknown(self, monkeypatch):
        self.tags(monkeypatch, error=AssertionError("must not be asked"))
        assert weights("anthropic", "https://api.anthropic.com", "m") is None

    def test_a_server_that_is_down_is_unknown_not_a_failure(self, monkeypatch):
        self.tags(monkeypatch, error=httpx.ConnectError("refused"))
        assert weights("ollama", "http://localhost:11434/v1", "m") is None

    def test_a_tag_it_does_not_list_is_unknown(self, monkeypatch):
        self.tags(monkeypatch, {"models": [{"name": "other", "digest": "x"}]})
        assert weights("ollama", "http://localhost:11434/v1", "m") is None


def run(at, *, weights_=None, definition=None, passes=1):
    return SuiteRun(
        suite="pkg 0.1", provider="ollama", model="qwen3.8:latest", at=at,
        repeat=1, cases_in_suite=1, package="aaa", weights=weights_,
        cases=[CaseRecord(id="x", passed=bool(passes), attempts=1,
                          passes=passes, min_pass_rate=1.0, tokens=10,
                          seconds=1.0, ran_model=True,
                          definition=definition)])


T1, T2, T3 = ("2026-09-01T00:00:00Z", "2026-09-02T00:00:00Z",
              "2026-09-03T00:00:00Z")


class TestThePool:
    def test_an_edited_case_is_one_row_per_definition(self):
        (group,) = pool([run(T1, definition="d1"), run(T2, definition="d2"),
                         run(T3, definition="d1")])
        rows = sorted((c.definition, c.runs) for c in group.cases)
        assert rows == [("d1", 2), ("d2", 1)]
        assert all(c.definitions == 2 for c in group.cases)

    def test_an_old_report_joins_the_only_known_definition(self):
        (group,) = pool([run(T1), run(T2, definition="d1")])
        (case,) = group.cases
        assert case.runs == 2 and case.definition == "d1"
        assert case.definitions == 0

    def test_a_re_pulled_tag_is_two_pools(self):
        pools = pool([run(T1, weights_="w1"), run(T2, weights_="w2")])
        assert sorted(p.weights for p in pools) == ["w1", "w2"]
        assert all(p.weights_split == 2 for p in pools)

    def test_unknown_weights_join_the_only_known_ones(self):
        (group,) = pool([run(T1), run(T2, weights_="w1")])
        assert group.weights == "w1" and group.weights_split == 0

    def test_both_are_said_out_loud(self, tmp_path, capsys):
        paths = []
        for n, r in enumerate((run(T1, weights_="w1", definition="d1"),
                               run(T2, weights_="w2", definition="d2"))):
            paths.append(str(tmp_path / f"{n}.json"))
            write_report(tmp_path / f"{n}.json", r)
        assert main(["--reports", *paths, "--pool"]) == 0
        out = " ".join(capsys.readouterr().out.split())
        assert out.count("pointed at 2 different weights") == 1
        assert "(weights w1)" in out and "(weights w2)" in out

    def test_an_edited_case_is_said_once_and_its_rows_run_oldest_first(
            self, tmp_path, capsys):
        paths = []
        for n, r in enumerate((run(T1, definition="zzz"),
                               run(T2, definition="aaa", passes=0))):
            paths.append(str(tmp_path / f"{n}.json"))
            write_report(tmp_path / f"{n}.json", r)
        main(["--reports", *paths, "--pool"])
        out = " ".join(capsys.readouterr().out.split())
        assert out.count("was edited between these runs") == 1
        assert out.index("(definition zzz)") < out.index("(definition aaa)")


class TestAgainst:
    def test_a_re_pulled_tag_is_named(self):
        assert compare(run(T1, weights_="w1"),
                       run(T2, weights_="w2")).weights_changed
        assert not compare(run(T1), run(T2, weights_="w2")).weights_changed

    def test_an_edited_case_is_marked_on_its_own_line(self):
        cmp = compare(run(T1, definition="d1"),
                      run(T2, definition="d2", passes=0))
        (delta,) = cmp.deltas
        assert delta.definition_changed
        assert not compare(run(T1), run(T2, definition="d2")) \
            .deltas[0].definition_changed

    def test_the_cli_says_both(self, tmp_path, capsys):
        before, after = tmp_path / "a.json", tmp_path / "b.json"
        write_report(before, run(T1, weights_="w1", definition="d1"))
        write_report(after, run(T2, weights_="w2", definition="d2", passes=0))
        assert main(["--reports", str(before), str(after)]) == 0
        out = " ".join(capsys.readouterr().out.split())
        assert "same tag, different weights: w1 → w2" in out
        assert "the case was edited between these runs" in out


class TestTheReport:
    def test_both_round_trip(self, tmp_path):
        path = tmp_path / "r.json"
        write_report(path, run(T1, weights_="w1", definition="d1"))
        back = read_report(path)
        assert back.weights == "w1" and back.cases[0].definition == "d1"

    def test_a_suite_run_writes_both(self, tmp_path, monkeypatch):
        import yantra.cli.main as cli_main
        from test_eval_cost import TestThroughTheGate
        gate = TestThroughTheGate()
        root = gate._suite_dir(tmp_path)
        monkeypatch.setattr(cli_main, "weights", lambda *a, **k: "w1")
        report = tmp_path / "r.json"
        gate._run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                                "ollama", "--model", "m", "--report",
                                str(report)])
        raw = json.loads(report.read_text())
        assert raw["weights"] == "w1"
        assert raw["cases"][0]["definition"] == fp_of(root)
