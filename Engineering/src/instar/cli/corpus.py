# SPDX-License-Identifier: Apache-2.0
"""``instar corpus``: read a measurement corpus across runs.

- ``instar corpus runs DIR``         the runs, with their judge and age
- ``instar corpus calls DIR``        the call records, filtered (JSONL out)
- ``instar corpus calibration DIR``  each judge's control-arm score, run by run
- ``instar corpus scores DIR``       every arm against its run's control
- ``instar corpus looks DIR``        how often each workload has been measured
- ``instar corpus rubric DIR --rubric FILE``  rubric archaeology: which
  dimensions have ever decided a verdict, and the range each metric has taken
- ``instar corpus labels DIR``       gold labels that independent models agree
  are wrong (label-ceiling detector)

All take the same filters, leave mock runs out unless ``--include-mock``,
and show how old every row is. ``--json`` prints machine-readable output.
Nothing here writes to the corpus.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from typing import Any

from instar.core.corpus import KIND_ARMS, KIND_REJUDGE, ROLE_CANDIDATE, ROLE_CONTROL
from instar.core.corpus_read import (
    DEFAULT_Z,
    CorpusFilter,
    age_days,
    calibration,
    dates_per_judge,
    iter_runs,
    judge_label,
    label_ceiling,
    looks,
    rubric_archaeology,
    scores,
    select_calls,
    select_runs,
)
from instar.rubrics.spec import FAIL, MARGINAL, PASS, UNMEASURED, Rubric


def _date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"{s!r} is not a YYYY-MM-DD date") from e


def _filter(args: argparse.Namespace) -> CorpusFilter:
    return CorpusFilter(
        tenant=args.tenant,
        workload=args.workload,
        kind=args.kind,
        since=args.since,
        until=args.until,
        judge_family=args.judge_family,
        judge_model=args.judge_model,
        feature=args.feature,
        model=args.model,
        role=getattr(args, "role", None),
        include_mock=args.include_mock,
    )


def _load(args: argparse.Namespace) -> Any:
    try:
        return iter_runs(args.corpus)
    except (FileNotFoundError, ValueError) as e:
        raise SystemExit(f"instar: {e}") from e


def _fmt(x: float | None, spec: str = ".3f") -> str:
    return "-" if x is None else format(x, spec)


def _table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    lines = ["  ".join(h.ljust(w) for h, w in zip(headers, widths, strict=True)).rstrip()]
    lines.append("  ".join("-" * w for w in widths))
    lines += ["  ".join(c.ljust(w) for c, w in zip(r, widths, strict=True)).rstrip() for r in rows]
    return "\n".join(lines)


def _oldest(ages: list[int]) -> str:
    return f"rows are {min(ages)}-{max(ages)} days old" if ages else "no rows"


def _cmd_runs(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    runs = select_runs(_load(args), _filter(args))
    if args.json:
        for r in runs:
            print(json.dumps({**r.record, "age_days": age_days(r.recorded_at, now)}))
        return 0
    if not runs:
        print("no runs match")
        return 0
    rows = [
        [
            r.recorded_at.strftime("%Y-%m-%d %H:%M"),
            str(age_days(r.recorded_at, now)),
            r.run_id,
            r.kind,
            r.tenant_id,
            r.workload_id or "-",
            judge_label(r.judge),
            str(r.record.get("n_records", "-")),
            r.source_run_id or "-",
            str(r.record.get("git_sha") or "-"),
        ]
        for r in runs
    ]
    headers = [
        "recorded (UTC)", "age d", "run", "kind", "tenant", "workload",
        "judge", "records", "source run", "build",
    ]  # fmt: skip
    print(_table(headers, rows))
    print(f"\n{len(runs)} runs; {_oldest([int(r[1]) for r in rows])}")
    return 0


def _cmd_calls(args: argparse.Namespace) -> int:
    n = 0
    for rec in select_calls(_load(args), _filter(args)):
        n += 1
        if not args.count:
            print(json.dumps(rec, ensure_ascii=False))
    if args.count:
        print(n)
    return 0


def _cmd_calibration(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    rows = calibration(_load(args), _filter(args), now=now, z=args.z)
    if args.json:
        for r in rows:
            print(json.dumps(r.to_json()))
        return 0
    if not rows:
        print("no judged runs with a scored control arm match")
        return 0
    table = [
        [
            r.judge,
            r.recorded_at.strftime("%Y-%m-%d"),
            str(r.age_days),
            r.run_id,
            r.kind,
            _fmt(r.control.mean),
            _fmt(r.control.se),
            f"{r.control.n_calls}/{r.control.n_samples}",
            _fmt(r.noise_band),
            r.gold_version or "-",
        ]
        for r in rows
    ]
    headers = [
        "judge", "date", "age d", "run", "kind", "control", "se",
        "calls/samples", f"band (z={args.z:g})", "gold",
    ]  # fmt: skip
    print(_table(headers, table))
    print(
        "\ncontrol = the judge's score for an answer from the baseline's own model; "
        "1.000 means it never marked a same-model answer down."
    )
    print(
        "band = how far two runs under that judge can differ by chance; "
        "a smaller gap between two models is not evidence."
    )
    single = sorted(j for j, n in dates_per_judge(rows).items() if n < 2)
    if single:
        print(
            f"drift: {len(single)} of {len(dates_per_judge(rows))} judges have one date only; "
            "a trend needs runs on two or more dates."
        )
    print(_oldest([r.age_days for r in rows]))
    return 0


def _cmd_scores(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    rows = scores(_load(args), _filter(args), now=now, z=args.z)
    if args.json:
        for r in rows:
            print(json.dumps(r.to_json()))
        return 0
    if not rows:
        print("no judged arms match")
        return 0
    table = [
        [
            r.judge,
            r.recorded_at.strftime("%Y-%m-%d"),
            str(r.age_days),
            r.run_id,
            r.arm,
            r.model or "-",
            _fmt(r.score.mean),
            f"{r.score.n_calls}/{r.score.n_samples}",
            _fmt(r.relative_to_control, ".2f"),
            _fmt(None if r.gap is None else r.gap.mean, "+.3f"),
            _fmt(r.band),
            r.verdict,
        ]
        for r in rows
    ]
    headers = [
        "judge", "date", "age d", "run", "arm", "model", "score", "calls/samples",
        "vs control", "gap", "band", "verdict",
    ]  # fmt: skip
    print(_table(headers, table))
    print(
        "\ngap = arm minus control, paired by sample; band = "
        f"{args.z:g} standard errors of that gap. Never compare scores across judges."
    )
    print(_oldest([r.age_days for r in rows]))
    return 0


def _cmd_looks(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    rows = looks(_load(args), _filter(args), now=now)
    if args.json:
        for r in rows:
            print(json.dumps(r.to_json()))
        return 0
    if not rows:
        print("no runs match")
        return 0
    table = [
        [
            r.tenant_id,
            r.workload_id or "-",
            r.gold_version or "-",
            ",".join(r.split_roles),
            str(r.arms_runs),
            str(r.rejudge_runs),
            r.first.strftime("%Y-%m-%d"),
            r.last.strftime("%Y-%m-%d"),
            str(r.age_days),
        ]
        for r in rows
    ]
    headers = [
        "tenant", "workload", "gold", "split role", "arms runs", "rejudges",
        "first", "last", "age d",
    ]  # fmt: skip
    print(_table(headers, table))
    print(
        "\nEvery arms run is a look at the tasks, and every decision taken on a set "
        "spends some of it.\nA 'standard' workload is the yardstick: measure on it, "
        "never tune against it."
    )
    unset = sum(1 for r in rows if "unset" in r.split_roles)
    if unset:
        print(f"{unset} workload(s) have runs with no --split-role recorded.")
    return 0


def _cmd_rubric(args: argparse.Namespace) -> int:
    try:
        rubric = Rubric.from_json(args.rubric)
    except (OSError, ValueError) as e:
        raise SystemExit(f"instar: {e}") from e
    rows, overall = rubric_archaeology(_load(args), _filter(args), rubric)
    if args.json:
        print(json.dumps({"rubric": rubric.name, "overall": overall}))
        for h in rows:
            print(json.dumps(h.to_json()))
        return 0
    if not rows:
        print("no candidate arms match")
        return 0
    table = [
        [
            h.dimension,
            h.metric,
            h.judge,
            str(h.n),
            " ".join(f"{(h.counts or {}).get(v, 0)}" for v in (PASS, MARGINAL, FAIL, UNMEASURED)),
            str(h.binding),
            str(h.sole),
            f"{_fmt(h.lo)}..{_fmt(h.hi)}",
            f"{h.newest_days}-{h.oldest_days}",
            "NEVER BINDING" if h.never_binding else "",
        ]
        for h in rows
    ]
    headers = [
        "dimension", "metric", "judge", "arms", "pass/marg/fail/unmeas", "binding",
        "sole", "range", "age d", "",
    ]  # fmt: skip
    print(f"rubric {rubric.name}: applied to stored arms, never re-run\n")
    print(_table(headers, table))
    tally = ", ".join(f"{v} {overall[v]}" for v in (PASS, MARGINAL, FAIL, UNMEASURED))
    print(f"\noverall verdicts: {tally}")
    print(
        "binding = at the worst level of a non-pass verdict; sole = the verdict would "
        "have been better had it passed.\nA dimension that is never binding has never "
        "changed an outcome: a candidate for deletion (RUBRICS.md §8)."
    )
    dims = [d.id for d in rubric.dimensions]
    never = [d for d in dims if all(h.never_binding for h in rows if h.dimension == d)]
    if never:
        print(f"never binding under any judge so far: {', '.join(never)}")
    unmeasured = [
        d
        for d in dims
        if all(h.counts and h.counts[UNMEASURED] == h.n for h in rows if h.dimension == d)
    ]
    if unmeasured:
        print(
            f"no value on any matching run: {', '.join(unmeasured)} (unjudged runs, "
            "unknown cost, or a routing metric where an arm.* metric was meant)"
        )
    return 0


def _cmd_labels(args: argparse.Namespace) -> int:
    now = datetime.now(UTC)
    labels = [x.strip() for x in args.labels.split(",") if x.strip()] if args.labels else None
    report = label_ceiling(
        _load(args), _filter(args), labels=labels, min_models=args.min_models, now=now
    )
    flags, examined = report.flags, report.examined
    if args.json:
        for f in flags:
            print(json.dumps(f.to_json()))
        return 0
    if not examined:
        print("no gold-labelled samples in matching arms runs")
        return 0
    if flags:
        table = [
            [
                f.run_id,
                str(f.age_days),
                f.sample_id,
                f.gold,
                f.consensus,
                f"{len(f.agreeing)}/{f.n_models}",
                f"{f.consistency:.2f}",
                ", ".join(f.agreeing),
            ]
            for f in flags
        ]
        headers = [
            "run", "age d", "sample", "gold", "models say", "agree", "consistency",
            "models",
        ]  # fmt: skip
        print(_table(headers, table))
        print()
    print(
        f"{len(flags)} flag(s) across {examined} gold-labelled sample(s). A flag means "
        f"{args.min_models}+ different models\ngave the same answer and the gold label "
        "disagrees: check the label before blaming the models.\nconsistency = the "
        "lowest share of repeats on which an agreeing model gave that answer."
    )
    print("\nagreement with gold (majority answer per sample; read after the flags):")
    for model, (hit, n) in report.accuracy.items():
        print(f"  {model:<32} {hit}/{n}")
    return 0


def _add_filters(p: argparse.ArgumentParser, *, role: bool = True) -> None:
    p.add_argument("corpus", help="corpus directory (the one given to --corpus when writing)")
    p.add_argument("--tenant", help="only this tenant's runs")
    p.add_argument("--workload", help="only this workload id")
    p.add_argument("--kind", choices=[KIND_ARMS, KIND_REJUDGE], help="only arms or rejudge runs")
    p.add_argument("--since", type=_date, help="only runs on or after YYYY-MM-DD (UTC)")
    p.add_argument("--until", type=_date, help="only runs on or before YYYY-MM-DD (UTC)")
    p.add_argument("--judge-family", help="only runs judged by this family (e.g. anthropic)")
    p.add_argument("--judge-model", help="only runs judged by this model")
    p.add_argument("--feature", help="only calls for this feature")
    p.add_argument("--model", help="only calls (or arms) for this requested or served model")
    if role:
        p.add_argument(
            "--role",
            choices=["baseline", ROLE_CONTROL, ROLE_CANDIDATE],
            help="only calls from arms in this role",
        )
    p.add_argument(
        "--include-mock", action="store_true", help="include mock runs (they measure nothing)"
    )
    p.add_argument("--json", action="store_true", help="print one JSON object per row")


def add_corpus_parser(sub: Any) -> None:
    """Register ``instar corpus`` and its four subcommands."""
    corpus = sub.add_parser(
        "corpus",
        help="read a measurement corpus across runs: runs, calls, judge calibration, scores",
        description="Read the runs a corpus holds, together. Every report shows how "
        "old its rows are, and leaves mock runs out unless asked.",
    )
    csub = corpus.add_subparsers(dest="corpus_cmd", required=True)

    runs = csub.add_parser("runs", help="list runs with their judge, age and provenance")
    _add_filters(runs, role=False)
    runs.set_defaults(func=_cmd_runs)

    calls = csub.add_parser("calls", help="print matching call records as JSONL")
    _add_filters(calls)
    calls.add_argument("--count", action="store_true", help="print only how many match")
    calls.set_defaults(func=_cmd_calls)

    cal = csub.add_parser(
        "calibration",
        help="each judge's score on the same-model control, run by run (drift over time)",
    )
    _add_filters(cal, role=False)
    cal.add_argument("--z", type=float, default=DEFAULT_Z, help="band width in standard errors")
    cal.set_defaults(func=_cmd_calibration)

    sc = csub.add_parser(
        "scores",
        help="every judged arm against its run's control, with a noise-band verdict",
    )
    _add_filters(sc)
    sc.add_argument("--z", type=float, default=DEFAULT_Z, help="band width in standard errors")
    sc.set_defaults(func=_cmd_scores)

    lk = csub.add_parser("looks", help="how many times each workload has been measured")
    _add_filters(lk, role=False)
    lk.set_defaults(func=_cmd_looks)

    rb = csub.add_parser(
        "rubric",
        help="rubric archaeology: apply a rubric to every stored arm; which dimensions "
        "ever decided a verdict",
    )
    _add_filters(rb, role=False)
    rb.add_argument("--rubric", required=True, help="rubric JSON (use arm.* metrics)")
    rb.set_defaults(func=_cmd_rubric)

    lb = csub.add_parser(
        "labels",
        help="label-ceiling detector: gold labels that independent models agree are wrong",
    )
    _add_filters(lb, role=False)
    lb.add_argument("--labels", help="comma-separated label set (default: the gold values)")
    lb.add_argument(
        "--min-models", type=int, default=2, help="distinct models that must agree (default 2)"
    )
    lb.set_defaults(func=_cmd_labels)


if __name__ == "__main__":  # pragma: no cover
    sys.exit("run as: instar corpus ...")
