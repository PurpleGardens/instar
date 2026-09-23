# SPDX-License-Identifier: Apache-2.0
"""Human grading: export a blind sheet, read the grades back, score a transcript.

The properties that matter: the sheet never reveals an arm or model; a grade
lands on exactly the pair it was given for, however the sheet was reordered;
an ungraded row is unscored rather than passed; and a sheet from another run
is refused instead of silently scoring nothing.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from instar.cli.main import main
from instar.core.arms import Arm, judge_calls, rejudge, run_arms
from instar.core.traffic import TrafficSample
from instar.core.transcript import Transcript
from instar.providers.base import CompletionResult
from instar.providers.mock import MockBackend
from instar.rubrics.human import (
    HUMAN_FAMILY,
    SHEET_COLUMNS,
    HumanJudge,
    grading_rows,
    load_grades,
    pair_id,
    write_grading_sheet,
)

STRONG = "mock-strong"
WEAK = "mock-weak"


def _samples(n: int = 4) -> list[TrafficSample]:
    return [
        TrafficSample(
            id=f"s{i}",
            feature="demo.feature",
            system="Answer briefly.",
            messages=[{"role": "user", "content": f"question {i}"}],
        )
        for i in range(n)
    ]


def _transcript(n: int = 4) -> Transcript:
    arms = [
        Arm("base", MockBackend("base", latency_s=0.001), STRONG),
        Arm("cheap", MockBackend("cheap", latency_s=0.001), WEAK),
        Arm("other", MockBackend("other", latency_s=0.001), "mock-other"),
    ]
    result = run_arms(_samples(n), arms=arms, capture=True)
    assert result.transcript is not None
    return result.transcript


def _read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def _write_sheet(path: Path, rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(SHEET_COLUMNS))
        w.writeheader()
        w.writerows(rows)


def _ok(text: str) -> CompletionResult:
    return CompletionResult(text=text, model="m", input_tokens=1, output_tokens=1, latency_s=0.0)


class TestSheet:
    def test_one_row_per_candidate_pair_and_no_provenance(self, tmp_path: Path) -> None:
        t = _transcript(4)
        out = tmp_path / "sheet.csv"
        n = write_grading_sheet(t, out)
        rows = _read_sheet(out)
        assert n == len(rows) == 8  # 4 prompts x 2 non-baseline arms
        assert tuple(rows[0]) == SHEET_COLUMNS
        # The mock backend writes its model id into the answer text itself, so
        # check every column the sheet adds, not the answers it carries.
        added = " ".join(
            " ".join((r["item_id"], r["feature"], r["task"], r["grade"], r["note"])) for r in rows
        )
        for leak in ("base", "cheap", "other", STRONG, WEAK, "mock-other"):
            assert leak not in added
            assert leak not in " ".join(SHEET_COLUMNS)
        assert all(r["grade"] == "" and r["note"] == "" for r in rows)
        assert "Answer briefly." in rows[0]["task"]

    def test_shuffle_is_seeded(self) -> None:
        t = _transcript(6)
        a = [r.item_id for r in grading_rows(t, seed=1)]
        b = [r.item_id for r in grading_rows(t, seed=1)]
        c = [r.item_id for r in grading_rows(t, seed=2)]
        assert a == b
        assert sorted(a) == sorted(c)
        assert a != c

    def test_identical_pairs_are_graded_once(self) -> None:
        t = _transcript(2)
        # Make both candidate arms say exactly what "cheap" said.
        for e in t.entries:
            e.completions["other"] = e.completions["cheap"]
        assert len(grading_rows(t)) == 2

    def test_failed_calls_are_not_offered_for_grading(self) -> None:
        t = _transcript(2)
        t.entries[0].completions["cheap"] = CompletionResult.failure(WEAK, "boom")
        assert len(grading_rows(t)) == 3

    def test_refuses_to_overwrite(self, tmp_path: Path) -> None:
        out = tmp_path / "sheet.csv"
        out.write_text("graded work\n")
        with pytest.raises(FileExistsError):
            write_grading_sheet(_transcript(), out)
        assert out.read_text() == "graded work\n"
        write_grading_sheet(_transcript(), out, overwrite=True)
        assert out.read_text() != "graded work\n"


class TestGrades:
    def test_blank_rows_skipped_and_case_ignored(self, tmp_path: Path) -> None:
        p = tmp_path / "g.csv"
        _write_sheet(
            p,
            [
                {"item_id": "a", "grade": "pass", "note": " fine "},
                {"item_id": "b", "grade": ""},
                {"item_id": "c", "grade": "Marginal"},
            ],
        )
        g = load_grades(p)
        assert set(g) == {"a", "c"}
        assert (g["a"].score, g["a"].note) == (1.0, "fine")
        assert g["c"].score == 0.5

    def test_unknown_grade_names_the_line(self, tmp_path: Path) -> None:
        p = tmp_path / "g.csv"
        _write_sheet(p, [{"item_id": "a", "grade": "PASS"}, {"item_id": "b", "grade": "ok"}])
        with pytest.raises(ValueError, match=r"g\.csv:3: grade 'OK'"):
            load_grades(p)

    def test_conflicting_duplicate_is_refused(self, tmp_path: Path) -> None:
        p = tmp_path / "g.csv"
        _write_sheet(p, [{"item_id": "a", "grade": "PASS"}, {"item_id": "a", "grade": "FAIL"}])
        with pytest.raises(ValueError, match="graded both PASS and FAIL"):
            load_grades(p)

    def test_missing_grade_column(self, tmp_path: Path) -> None:
        p = tmp_path / "g.csv"
        p.write_text("item_id,score\na,1\n", encoding="utf-8")
        with pytest.raises(ValueError, match="missing column"):
            load_grades(p)


class TestJudge:
    def test_key_is_human_and_blind(self) -> None:
        k = HumanJudge({}, "grader-1").key()
        assert (k.kind, k.model, k.family, k.blind) == ("human", "grader-1", HUMAN_FAMILY, True)

    def test_rejects_email_and_empty_grader(self) -> None:
        with pytest.raises(ValueError, match="email"):
            HumanJudge({}, "someone@example.com")
        with pytest.raises(ValueError, match="grader id"):
            HumanJudge({}, "  ")

    def test_ungraded_pair_is_skipped_not_scored(self) -> None:
        s = _samples(2)
        base = [_ok("ref 0"), _ok("ref 1")]
        cand = [_ok("cand 0"), _ok("cand 1")]
        from instar.rubrics.human import Grade

        judge = HumanJudge({pair_id(s[0], base[0], cand[0]): Grade(0.0, "FAIL", "")}, "g1")
        out = judge_calls(judge, s, base, cand)
        assert out[0] is not None and out[0].score == 0.0
        assert out[1] is None

    def test_rejudge_end_to_end(self, tmp_path: Path) -> None:
        t = _transcript(4)
        sheet = tmp_path / "sheet.csv"
        write_grading_sheet(t, sheet)
        rows = _read_sheet(sheet)
        # Grade every "cheap" answer FAIL and every "other" answer PASS, leaving
        # one "other" row blank. Look the arm up by content, as a test may.
        cheap_ids = {
            pair_id(e.sample, e.completions["base"], e.completions["cheap"]) for e in t.entries
        }
        blank = None
        for r in rows:
            if r["item_id"] in cheap_ids:
                r["grade"] = "FAIL"
            elif blank is None:
                blank = r["item_id"]
            else:
                r["grade"] = "PASS"
        rows.reverse()  # order must not matter
        _write_sheet(sheet, rows)

        judge = HumanJudge.for_transcript(t, sheet, "grader-1")
        result = rejudge(t, judge)
        cheap = result.by_name("cheap")
        other = result.by_name("other")
        assert (cheap.quality_mean, cheap.quality_n) == (0.0, 4)
        assert (other.quality_mean, other.quality_n) == (1.0, 3)
        assert any("1 pair(s) were not scored" in w for w in result.warnings)
        assert result.judge is not None and result.judge.family == HUMAN_FAMILY

    def test_sheet_from_another_run_is_refused(self, tmp_path: Path) -> None:
        sheet = tmp_path / "sheet.csv"
        _write_sheet(sheet, [{"item_id": "0123456789abcdef", "grade": "PASS"}])
        with pytest.raises(ValueError, match="not in this transcript"):
            HumanJudge.for_transcript(_transcript(), sheet, "g1")


class TestCli:
    def test_grade_sheet_then_rejudge(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        t = _transcript(3)
        tpath = t.save(tmp_path / "transcript.json")
        sheet = tmp_path / "sheet.csv"
        assert main(["grade-sheet", str(tpath), "-o", str(sheet)]) == 0
        rows = _read_sheet(sheet)
        for r in rows:
            r["grade"] = "MARGINAL"
        _write_sheet(sheet, rows)

        runs = tmp_path / "runs"
        rc = main(
            [
                "rejudge",
                str(tpath),
                "--grades",
                str(sheet),
                "--grader",
                "grader 1",
                "--runs-dir",
                str(runs),
            ]
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "judge: human (grader 1)" in out
        result = json.loads((runs / "rejudge-human-grader-1" / "result.json").read_text())
        assert result["judge"]["family"] == HUMAN_FAMILY

    def test_grade_sheet_refuses_to_overwrite(self, tmp_path: Path) -> None:
        tpath = _transcript().save(tmp_path / "transcript.json")
        sheet = tmp_path / "sheet.csv"
        sheet.write_text("keep me")
        with pytest.raises(SystemExit, match="--force"):
            main(["grade-sheet", str(tpath), "-o", str(sheet)])

    def test_grades_need_a_grader(self, tmp_path: Path) -> None:
        tpath = _transcript().save(tmp_path / "transcript.json")
        with pytest.raises(SystemExit, match="--grader"):
            main(["rejudge", str(tpath), "--grades", str(tmp_path / "x.csv")])

    def test_grades_exclude_model_judges(self, tmp_path: Path) -> None:
        tpath = _transcript().save(tmp_path / "transcript.json")
        with pytest.raises(SystemExit, match="cannot be combined"):
            main(
                [
                    "rejudge",
                    str(tpath),
                    "--grades",
                    "x.csv",
                    "--grader",
                    "g1",
                    "--mock-judge",
                ]
            )
