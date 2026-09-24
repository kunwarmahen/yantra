"""A package's fingerprint, and a version nobody bumped.

The bias here is a pool that trusts the version string. An author edits
``prompt.md``, forgets the bump, and a week of the old prompt pools with a
week of the new one as if they were samples of one agent. The tests pin:

* an edit the agent is built from changes the fingerprint -- prompt,
  tools, skills, a file the agent reads, a tool dir outside the package;
* what is not the agent does not -- ``evals/``, hidden files (the session
  database changes every run), ``__pycache__``;
* one label with two fingerprints is two pools, said out loud;
* a report too old to have a fingerprint is unknown, never "different";
* ``--against`` names an edit made under the same version.
"""

from __future__ import annotations

from pathlib import Path

from yantra.cli.main import main
from yantra.eval_report import (CaseRecord, SuiteRun, compare, pool,
                                read_report, write_report)
from yantra.fingerprint import fingerprint
from yantra.package import load_package


def package(tmp_path, *, manifest='[agent]\nname = "pkg"\nversion = "0.1"\n'):
    root = tmp_path / "pkg"
    root.mkdir(exist_ok=True)
    (root / "agent.toml").write_text(manifest)
    (root / "prompt.md").write_text("Be brief.")
    return root


def fp(root) -> str:
    return fingerprint(load_package(Path(root)))


class TestWhatChangesIt:
    def test_the_same_files_give_the_same_fingerprint(self, tmp_path):
        root = package(tmp_path)
        assert fp(root) == fp(root)
        assert len(fp(root)) == 12

    def test_an_edited_prompt_changes_it(self, tmp_path):
        root = package(tmp_path)
        before = fp(root)
        (root / "prompt.md").write_text("Be thorough.")
        assert fp(root) != before

    def test_a_file_the_agent_only_reads_changes_it(self, tmp_path):
        root = package(tmp_path)
        (root / "sources").mkdir()
        (root / "sources" / "a.md").write_text("one")
        before = fp(root)
        (root / "sources" / "a.md").write_text("two")
        assert fp(root) != before

    def test_a_tool_dir_outside_the_package_is_hashed_too(self, tmp_path):
        shared = tmp_path / "shared_tools"
        shared.mkdir()
        (shared / "t.py").write_text("# v1")
        root = package(tmp_path, manifest=(
            '[agent]\nname = "pkg"\n[tools]\ndirs = ["../shared_tools"]\n'))
        before = fp(root)
        (shared / "t.py").write_text("# v2")
        assert fp(root) != before


class TestWhatDoesNot:
    def test_evals_are_the_grading_side(self, tmp_path):
        root = package(tmp_path)
        (root / "evals").mkdir()
        (root / "evals" / "cases.toml").write_text("# one")
        before = fp(root)
        (root / "evals" / "cases.toml").write_text("# two")
        assert fp(root) == before

    def test_the_session_database_and_bytecode_are_not_the_agent(
            self, tmp_path):
        root = package(tmp_path)
        before = fp(root)
        (root / ".yantra").mkdir()
        (root / ".yantra" / "session.sqlite3").write_bytes(b"\0\1")
        (root / "tools" / "__pycache__").mkdir(parents=True)
        (root / "tools" / "__pycache__" / "t.cpython-312.pyc").write_bytes(b"x")
        assert fp(root) == before

    def test_an_agent_with_no_package_has_none(self):
        from yantra.spec import AgentSpec
        assert fingerprint(AgentSpec()) is None


def run(package_fp, at, passes=1):
    return SuiteRun(
        suite="pkg 0.1", provider="p", model="m", at=at, repeat=1,
        cases_in_suite=1, package=package_fp,
        cases=[CaseRecord(id="x", passed=bool(passes), attempts=1,
                          passes=passes, min_pass_rate=1.0, tokens=10,
                          seconds=1.0, ran_model=True)])


class TestThePool:
    def test_one_version_two_packages_is_two_pools(self):
        pools = pool([run("aaa", "2026-09-01T00:00:00Z"),
                      run("bbb", "2026-09-02T00:00:00Z"),
                      run("aaa", "2026-09-03T00:00:00Z")])
        assert sorted((p.package, len(p.runs)) for p in pools) == [
            ("aaa", 2), ("bbb", 1)]
        assert all(p.split_from == 2 for p in pools)

    def test_an_old_report_joins_the_only_known_package(self):
        (group,) = pool([run(None, "2026-09-01T00:00:00Z"),
                         run("aaa", "2026-09-02T00:00:00Z")])
        assert group.package == "aaa" and group.unknown == 1
        assert group.split_from == 0

    def test_old_reports_cannot_be_placed_when_there_are_two_packages(self):
        pools = pool([run(None, "2026-09-01T00:00:00Z"),
                      run("aaa", "2026-09-02T00:00:00Z"),
                      run("bbb", "2026-09-03T00:00:00Z")])
        assert sorted(p.package or "" for p in pools) == ["", "aaa", "bbb"]

    def test_reports_before_fingerprints_pool_as_they_always_did(self):
        (group,) = pool([run(None, "2026-09-01T00:00:00Z"),
                         run(None, "2026-09-02T00:00:00Z")])
        assert group.package is None and group.split_from == 0

    def test_the_split_is_said_out_loud(self, tmp_path, capsys):
        paths = []
        for n, (f, at) in enumerate((("aaa", "2026-09-01T00:00:00Z"),
                                     ("bbb", "2026-09-02T00:00:00Z"))):
            paths.append(str(tmp_path / f"{n}.json"))
            write_report(tmp_path / f"{n}.json", run(f, at))
        assert main(["--reports", *paths, "--pool"]) == 0
        out = " ".join(capsys.readouterr().out.split())
        assert out.count("ran as 2 different packages under one version") == 1
        assert "different suite/model pairs" not in out   # it is ONE pair
        assert "(package aaa)" in out and "(package bbb)" in out


class TestTheReport:
    def test_the_fingerprint_round_trips(self, tmp_path):
        path = tmp_path / "r.json"
        write_report(path, run("aaa", "2026-09-01T00:00:00Z"))
        assert read_report(path).package == "aaa"

    def test_against_names_an_edit_under_the_same_version(self):
        cmp = compare(run("aaa", "2026-09-01T00:00:00Z"),
                      run("bbb", "2026-09-02T00:00:00Z", passes=0))
        assert cmp.package_changed

    def test_an_old_report_is_not_evidence_of_an_edit(self):
        cmp = compare(run(None, "2026-09-01T00:00:00Z"),
                      run("bbb", "2026-09-02T00:00:00Z"))
        assert not cmp.package_changed

    def test_a_suite_run_writes_its_fingerprint(self, tmp_path, monkeypatch):
        from test_eval_cost import TestThroughTheGate
        gate = TestThroughTheGate()
        root = gate._suite_dir(tmp_path)
        report = tmp_path / "r.json"
        gate._run(monkeypatch, ["--agent", str(root), "--eval", "--provider",
                                "ollama", "--model", "m", "--report",
                                str(report)])
        assert read_report(report).package == fp(root)
