# SPDX-License-Identifier: Apache-2.0
"""The measurement corpus: runs appended with enough context to read them later.

What these tests protect, in order of how badly a silent failure would hurt:

- a stored score always says which judge produced it;
- a re-judge adds records next to the originals and never replaces them;
- a row is only eligible for pooling if its tenant opted in;
- repeats, roles, and the control arm survive into the records.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from instar.cli.main import main
from instar.core.arms import Arm, rejudge, run_arms
from instar.core.corpus import (
    ORIGIN_FAILURE_MINED,
    ROLE_BASELINE,
    ROLE_CANDIDATE,
    ROLE_CONTROL,
    RecordContext,
    find_corpus_root,
    load_run_context,
    write_run,
)
from instar.core.traffic import TrafficSample
from instar.core.transcript import Transcript
from instar.providers.base import Backend
from instar.providers.mock import MockBackend
from instar.rubrics.base import JudgeKey, model_family
from instar.rubrics.judges import BlindPairwiseJudge, LLMJudge, MockJudge

STRONG = "mock-strong"
WEAK = "mock-weak"
NOW = datetime(2026, 9, 21, 12, 0, 0, tzinfo=UTC)


def _samples(n: int = 3, **meta: Any) -> list[TrafficSample]:
    return [
        TrafficSample(
            id=f"s{i}",
            feature="demo.feature",
            messages=[{"role": "user", "content": f"question {i}"}],
            meta=dict(meta),
        )
        for i in range(n)
    ]


def _arms(control: bool = True) -> list[Arm]:
    arms = [
        Arm("base", MockBackend("base", latency_s=0.001), STRONG),
        Arm("cheap", MockBackend("cheap", latency_s=0.001), WEAK),
    ]
    if control:
        arms.append(Arm("ctl", MockBackend("ctl", latency_s=0.001), STRONG, is_control=True))
    return arms


def _run(samples: list[TrafficSample] | None = None, *, repeats: int = 2, control: bool = True):
    return run_arms(
        samples or _samples(),
        arms=_arms(control),
        repeats=repeats,
        judge=MockJudge(),
        capture=True,
    )


def _records(run_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (run_dir / "calls.jsonl").read_text().splitlines()]


def _ctx(**kw: Any) -> RecordContext:
    return RecordContext(**{"tenant_id": "tenant-a", "workload_id": "demo", **kw})


class TestJudgeKey:
    def test_model_judges_carry_model_and_family(self) -> None:
        backend: Backend = MockBackend("j")
        key = LLMJudge(backend, "claude-sonnet-4-6").key()
        assert key == JudgeKey(kind="llm", model="claude-sonnet-4-6", family="anthropic")

    def test_blind_judge_says_it_is_blind(self) -> None:
        key = BlindPairwiseJudge(MockBackend("j"), "openai/gpt-4o").key()
        assert key.blind is True
        assert key.family == "openai"

    def test_explicit_family_beats_the_guess(self) -> None:
        key = LLMJudge(MockBackend("j"), "my-finetune", family="alibaba").key()
        assert key.family == "alibaba"

    def test_objective_judges_have_no_model(self) -> None:
        assert MockJudge().key() == JudgeKey(kind="mock")

    @pytest.mark.parametrize(
        ("model", "family"),
        [
            ("claude-haiku-4-5", "anthropic"),
            ("anthropic/claude-sonnet-4.6", "anthropic"),
            ("gpt-4o-mini", "openai"),
            ("google/gemini-2.5-flash", "google"),
            ("gemma3:4b", "google"),
            ("qwen2.5:7b", "alibaba"),
            ("meta-llama/llama-3.1-8b-instruct", "meta"),
            ("something-new", "unknown"),
        ],
    )
    def test_family_guess(self, model: str, family: str) -> None:
        assert model_family(model) == family

    def test_result_json_carries_the_judge(self) -> None:
        d = _run().to_json()
        assert d["judge"] == {"kind": "mock", "model": None, "family": None, "blind": False}
        assert d["control"] == "ctl"


class TestControlArm:
    def test_control_must_serve_the_baselines_model(self) -> None:
        arms = [
            Arm("base", MockBackend("base"), STRONG),
            Arm("ctl", MockBackend("ctl"), WEAK, is_control=True),
        ]
        with pytest.raises(ValueError, match="must serve the baseline's model"):
            run_arms(_samples(), arms=arms)

    def test_only_one_control(self) -> None:
        arms = [
            Arm("base", MockBackend("base"), STRONG),
            Arm("c1", MockBackend("c1"), STRONG, is_control=True),
            Arm("c2", MockBackend("c2"), STRONG, is_control=True),
        ]
        with pytest.raises(ValueError, match="at most one control"):
            run_arms(_samples(), arms=arms)

    def test_baseline_cannot_be_the_control(self) -> None:
        arms = [
            Arm("base", MockBackend("base"), STRONG, is_control=True),
            Arm("other", MockBackend("other"), STRONG),
        ]
        with pytest.raises(ValueError, match="baseline cannot also be the control"):
            run_arms(_samples(), arms=arms)

    def test_a_judged_run_without_a_control_warns(self) -> None:
        result = _run(control=False)
        assert any("no control arm" in w for w in result.warnings)

    def test_a_judged_run_with_a_control_does_not(self) -> None:
        assert not any("no control arm" in w for w in _run().warnings)

    def test_the_control_is_judged_like_any_other_arm(self) -> None:
        result = _run()
        assert result.by_name("ctl").quality_n == 6


class TestWriteRun:
    def test_layout_is_tenant_then_date_then_run(self, tmp_path: Path) -> None:
        result = _run()
        assert result.transcript is not None
        run_dir = write_run(tmp_path, result, result.transcript, _ctx(), now=NOW)
        rel = run_dir.relative_to(tmp_path)
        assert rel.parts[:3] == ("tenant-a", "2026", "09")
        assert {p.name for p in run_dir.iterdir()} == {"run.json", "calls.jsonl", "transcript.json"}

    def test_one_record_per_entry_per_arm(self, tmp_path: Path) -> None:
        result = _run(repeats=2)
        assert result.transcript is not None
        rows = _records(write_run(tmp_path, result, result.transcript, _ctx(), now=NOW))
        assert len(rows) == 3 * 2 * 3  # samples x repeats x arms

    def test_roles_repeats_and_scores(self, tmp_path: Path) -> None:
        result = _run(repeats=2)
        assert result.transcript is not None
        rows = _records(write_run(tmp_path, result, result.transcript, _ctx(), now=NOW))
        roles = {r["arm"]: r["role"] for r in rows}
        assert roles == {"base": ROLE_BASELINE, "cheap": ROLE_CANDIDATE, "ctl": ROLE_CONTROL}
        assert {r["sample_index"] for r in rows} == {0, 1}
        base = [r for r in rows if r["role"] == ROLE_BASELINE]
        assert all(r["score"] is None and r["judge"] is None for r in base)
        judged = [r for r in rows if r["role"] != ROLE_BASELINE]
        assert all(r["score"] is not None for r in judged)
        assert all(r["judge"]["kind"] == "mock" for r in judged)

    def test_records_point_at_their_generation(self, tmp_path: Path) -> None:
        result = _run()
        assert result.transcript is not None
        run_dir = write_run(tmp_path, result, result.transcript, _ctx(), now=NOW)
        transcript = Transcript.load(run_dir / "transcript.json")
        for r in _records(run_dir):
            assert r["generation"]["run_id"] == run_dir.name
            entry = transcript.entries[r["generation"]["entry_index"]]
            assert entry.sample.id == r["sample_id"]
            assert entry.repeat == r["sample_index"]

    def test_run_record_carries_provenance_and_context(self, tmp_path: Path) -> None:
        result = _run()
        assert result.transcript is not None
        ctx = _ctx(rubric_version="r1", gold_version="g2")
        run_dir = write_run(tmp_path, result, result.transcript, ctx, now=NOW)
        run = json.loads((run_dir / "run.json").read_text())
        assert run["kind"] == "arms"
        assert run["instar_version"]
        assert "git_sha" in run
        assert run["recorded_at"] == "2026-09-21T12:00:00+00:00"
        assert (run["rubric_version"], run["gold_version"]) == ("r1", "g2")
        assert run["control"] == "ctl"
        assert load_run_context(run_dir) == ctx

    def test_never_overwrites(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        result = _run()
        assert result.transcript is not None
        monkeypatch.setattr("instar.core.corpus.new_run_id", lambda now: "fixed")
        write_run(tmp_path, result, result.transcript, _ctx(), now=NOW)
        with pytest.raises(FileExistsError):
            write_run(tmp_path, result, result.transcript, _ctx(), now=NOW)


class TestConsentOriginReaction:
    def _rows(self, tmp_path: Path, ctx: RecordContext, **meta: Any) -> list[dict[str, Any]]:
        result = _run(_samples(**meta), repeats=1)
        assert result.transcript is not None
        return _records(write_run(tmp_path, result, result.transcript, ctx, now=NOW))

    def test_consent_is_off_by_default(self, tmp_path: Path) -> None:
        assert not any(r["upstream_consent"] for r in self._rows(tmp_path, _ctx()))

    def test_tenant_consent_flows_to_rows(self, tmp_path: Path) -> None:
        rows = self._rows(tmp_path, _ctx(upstream_consent=True))
        assert all(r["upstream_consent"] for r in rows)

    def test_a_row_can_opt_out(self, tmp_path: Path) -> None:
        rows = self._rows(tmp_path, _ctx(upstream_consent=True), upstream_consent=False)
        assert not any(r["upstream_consent"] for r in rows)

    def test_a_row_cannot_opt_in_for_its_tenant(self, tmp_path: Path) -> None:
        rows = self._rows(tmp_path, _ctx(upstream_consent=False), upstream_consent=True)
        assert not any(r["upstream_consent"] for r in rows)

    def test_sample_origin_overrides_the_run_default(self, tmp_path: Path) -> None:
        rows = self._rows(tmp_path, _ctx(), origin=ORIGIN_FAILURE_MINED)
        assert {r["origin"] for r in rows} == {ORIGIN_FAILURE_MINED}

    def test_reaction_is_recorded(self, tmp_path: Path) -> None:
        rows = self._rows(tmp_path, _ctx(), reaction="regenerated")
        assert {r["reaction"] for r in rows} == {"regenerated"}

    def test_unknown_reaction_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError, match="reaction"):
            self._rows(tmp_path, _ctx(), reaction="loved-it")

    @pytest.mark.parametrize("tenant", ["", "../escape", "has space", "a/b"])
    def test_bad_tenant_ids_are_refused(self, tenant: str) -> None:
        with pytest.raises(ValueError, match="tenant_id"):
            RecordContext(tenant_id=tenant)

    def test_unknown_origin_is_refused(self) -> None:
        with pytest.raises(ValueError, match="origin"):
            RecordContext(tenant_id="t", origin="vibes")


class TestRejudge:
    def test_rejudge_appends_and_points_at_the_source(self, tmp_path: Path) -> None:
        result = _run()
        assert result.transcript is not None
        src = write_run(tmp_path, result, result.transcript, _ctx(), now=NOW)
        transcript = Transcript.load(src / "transcript.json")
        again = rejudge(transcript, MockJudge())
        new = write_run(tmp_path, again, transcript, load_run_context(src), source_run_dir=src)

        assert new != src
        assert not (new / "transcript.json").exists()
        run = json.loads((new / "run.json").read_text())
        assert (run["kind"], run["source_run_id"]) == ("rejudge", src.name)
        for r in _records(new):
            assert r["generation"]["run_id"] == src.name
            assert (tmp_path / r["generation"]["path"]) == src / "transcript.json"
        # the original is untouched
        assert len(_records(src)) == len(_records(new))

    def test_rejudge_keeps_the_control_role(self, tmp_path: Path) -> None:
        result = _run()
        assert result.transcript is not None
        again = rejudge(result.transcript, MockJudge())
        assert again.control == "ctl"
        assert not any("no control arm" in w for w in again.warnings)

    def test_find_corpus_root(self, tmp_path: Path) -> None:
        result = _run()
        assert result.transcript is not None
        src = write_run(tmp_path, result, result.transcript, _ctx(), now=NOW)
        assert find_corpus_root(src) == tmp_path.resolve()
        assert find_corpus_root(tmp_path) is None


class TestCli:
    def test_arms_writes_to_the_corpus(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        rc = main(
            [
                "arms",
                "--judge",
                "--control",
                "--corpus",
                str(corpus),
                "--tenant",
                "demo",
                "--runs-dir",
                str(tmp_path / "runs"),
            ]
        )
        assert rc == 0
        runs = list(corpus.rglob("run.json"))
        assert len(runs) == 1
        run = json.loads(runs[0].read_text())
        assert run["control"] == "control"
        assert run["mock"] is True
        assert run["workload_id"]

    def test_corpus_requires_a_tenant(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="--tenant"):
            main(["arms", "--corpus", str(tmp_path), "--runs-dir", str(tmp_path)])

    def test_rejudge_into_the_corpus(self, tmp_path: Path) -> None:
        corpus = tmp_path / "corpus"
        runs = str(tmp_path / "runs")
        main(
            [
                "arms",
                "--judge",
                "--control",
                "--corpus",
                str(corpus),
                "--tenant",
                "demo",
                "--runs-dir",
                runs,
            ]
        )
        transcript = next(corpus.rglob("transcript.json"))
        rc = main(
            [
                "rejudge",
                str(transcript),
                "--mock-judge",
                "--corpus",
                str(corpus),
                "--runs-dir",
                runs,
            ]
        )
        assert rc == 0
        kinds = sorted(json.loads(p.read_text())["kind"] for p in corpus.rglob("run.json"))
        assert kinds == ["arms", "rejudge"]

    def test_rejudge_refuses_a_transcript_from_outside_the_corpus(self, tmp_path: Path) -> None:
        result = _run()
        assert result.transcript is not None
        loose = result.transcript.save(tmp_path / "loose" / "transcript.json")
        with pytest.raises(SystemExit, match=r"needs a transcript\.json from a run inside"):
            main(
                [
                    "rejudge",
                    str(loose),
                    "--mock-judge",
                    "--corpus",
                    str(tmp_path / "corpus"),
                    "--runs-dir",
                    str(tmp_path),
                ]
            )

    def test_control_name_collision_is_refused(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit, match="rename yours"):
            main(
                [
                    "arms",
                    "--live",
                    "--control",
                    "--arm",
                    "name=control,model=m1,url=http://x",
                    "--arm",
                    "name=b,model=m2,url=http://y",
                    "--runs-dir",
                    str(tmp_path),
                ]
            )
