"""Pipeline analytics over the real tracker.

Reads a markdown pipeline table and reports the funnel. The interesting part
is not the arithmetic, it is that the source data has the exact defect this
engine exists to talk about: **stage vocabulary drift.**

533 rows accumulated over six months carry more than a dozen distinct status
values for what should be about five stages, in two languages, with three
different ways of writing the same thing. That is not sloppiness, it is what
every real CRM looks like after six months without an owned vocabulary, and
it is why "define the stage once" is a real engineering position rather than
a platitude.

So this module does two things and keeps them separate:

  1. report what the data literally says
  2. map it to canonical stages, and declare exactly what it had to guess

It never silently normalises. Anything it cannot map is counted in an
`unmapped` bucket and printed, because a funnel that hides its own coverage
is the thing being argued against.

Privacy: the tracker holds company names, contacts and notes and is not in
this repo. Point this at a local file. `--export-aggregate` writes counts
only, which is the artifact that is safe to publish.

    python src/funnel.py                          uses the bundled sample
    python src/funnel.py path/to/applications.md
    python src/funnel.py path/to/applications.md --export-aggregate out/funnel.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|")

# Canonical stages, in order. Deliberately few. The whole argument is that a
# pipeline needs a small owned vocabulary, so this list is short on purpose.
STAGES = ["sourced", "screened_out", "evaluated", "pack_built", "applied", "in_process"]

# Every literal value observed in the real tracker, mapped to a canonical
# stage. Mixed languages are not a mistake in this table, they are the data.
STATUS_MAP: dict[str, str] = {
    # evaluated
    "evaluada": "evaluated",
    "evaluated": "evaluated",
    "scored": "evaluated",
    # screened out by the gate, before any effort was spent
    "no aplicar": "screened_out",
    "skip": "screened_out",
    "no apply": "screened_out",
    "do not apply": "screened_out",
    # artifacts produced
    "pack built": "pack_built",
    "full pack built": "pack_built",
    "pack built (auto-build)": "pack_built",
    # submitted
    "aplicado": "applied",
    "applied": "applied",
    "re-applied": "applied",
    "reaplicado": "applied",
    # live
    "interview": "in_process",
    "entrevista": "in_process",
    "screening": "in_process",
    "assessment": "in_process",
    "offer": "in_process",
}

PLATFORMS = [
    "greenhouse", "ashby", "lever", "workday", "icims", "dayforce", "oracle",
    "eightfold", "rippling", "ultipro", "taleo", "smartrecruiters", "jobvite",
    "bamboohr", "adp", "gem", "phenom", "successfactors", "paylocity",
    "jazzhr", "recruitee", "teamtailor", "pinpoint", "breezy", "workable",
]


@dataclass
class Row:
    number: int
    date: str
    company: str
    role: str
    score: float | None
    status_raw: str
    notes: str

    @property
    def month(self) -> str:
        return self.date[:7]

    @property
    def stage(self) -> str | None:
        key = re.sub(r"[*_`]", "", self.status_raw).strip().lower()
        key = re.sub(r"\s+", " ", key)
        if key in STATUS_MAP:
            return STATUS_MAP[key]
        # Prefix match, because real values carry trailing commentary such as
        # "PACK BUILT (deck pending)".
        for literal, stage in STATUS_MAP.items():
            if key.startswith(literal):
                return stage
        return None


def parse(path: Path) -> list[Row]:
    rows: list[Row] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.split("|")]
        if len(cells) < 8:
            continue
        score = None
        if m := re.search(r"([\d.]+)\s*/\s*5", cells[5]):
            try:
                score = float(m.group(1))
            except ValueError:
                score = None
        rows.append(Row(
            number=int(cells[1]),
            date=cells[2],
            company=re.sub(r"[*]", "", cells[3])[:60],
            role=cells[4][:70],
            score=score,
            status_raw=cells[6],
            notes=cells[9] if len(cells) > 9 else "",
        ))
    return rows


def summarise(rows: list[Row]) -> dict:
    stages = Counter()
    unmapped = Counter()
    for r in rows:
        stage = r.stage
        if stage:
            stages[stage] += 1
        else:
            unmapped[re.sub(r"[*_`]", "", r.status_raw).strip()[:44] or "(blank)"] += 1

    platforms = Counter()
    blob = " ".join(r.notes.lower() for r in rows)
    for name in PLATFORMS:
        n = len(re.findall(r"\b" + name + r"\b", blob))
        if n:
            platforms[name] = n

    scores = [r.score for r in rows if r.score is not None]
    by_month = Counter(r.month for r in rows)

    return {
        "total_rows": len(rows),
        "distinct_status_literals": len({
            re.sub(r"[*_`]", "", r.status_raw).strip().lower() for r in rows
        }),
        "stages": dict(stages),
        "unmapped": dict(unmapped),
        "mapped_pct": round(100 * sum(stages.values()) / len(rows), 1) if rows else 0.0,
        "by_month": dict(sorted(by_month.items())),
        "platforms": dict(platforms.most_common()),
        "distinct_platforms": len(platforms),
        "scored_rows": len(scores),
        "score_mean": round(sum(scores) / len(scores), 2) if scores else None,
        "score_at_or_above_4": sum(1 for s in scores if s >= 4.0),
        "companies": len({r.company.lower() for r in rows}),
    }


def bar(n: int, top: int, width: int = 34) -> str:
    return "#" * max(1, int(width * n / top)) if n else ""


def report(rows: list[Row]) -> dict:
    s = summarise(rows)
    print("=" * 84)
    print("  PIPELINE FUNNEL".center(84))
    print("=" * 84)
    print(f"  rows            : {s['total_rows']}")
    print(f"  distinct companies: {s['companies']}")
    print(f"  months covered  : {len(s['by_month'])} "
          f"({min(s['by_month'])} to {max(s['by_month'])})")

    print("\n  BY MONTH")
    top = max(s["by_month"].values())
    for month, n in s["by_month"].items():
        print(f"    {month}  {bar(n, top)} {n}")

    print("\n  CANONICAL STAGES")
    order = [st for st in STAGES if st in s["stages"]]
    top = max(s["stages"].values()) if s["stages"] else 1
    for stage in order:
        n = s["stages"][stage]
        print(f"    {stage:<14} {bar(n, top)} {n}")

    print(f"\n  STAGE VOCABULARY DRIFT")
    print(f"    distinct status literals in the data : {s['distinct_status_literals']}")
    print(f"    canonical stages they should map to  : {len(STAGES)}")
    print(f"    rows mapped by the table             : {s['mapped_pct']}%")
    if s["unmapped"]:
        print(f"    UNMAPPED, reported rather than absorbed:")
        for literal, n in sorted(s["unmapped"].items(), key=lambda kv: -kv[1])[:10]:
            print(f"      {n:>4}  {literal}")

    print("\n  ATS PLATFORMS SEEN")
    print(f"    distinct platforms: {s['distinct_platforms']}")
    top = max(s["platforms"].values()) if s["platforms"] else 1
    for name, n in list(s["platforms"].items())[:12]:
        print(f"    {name:<16} {bar(n, top, 26)} {n}")

    print("\n  SCORING")
    print(f"    rows carrying a score : {s['scored_rows']}")
    if s["score_mean"] is not None:
        print(f"    mean score            : {s['score_mean']}")
        print(f"    at or above 4.0       : {s['score_at_or_above_4']}")

    print("\n  WHAT THIS CANNOT TELL YOU, STATED PLAINLY")
    print("    Submission is recorded inconsistently, so the applied count above is a")
    print("    floor and not a total. Interview outcomes are not in this table at all.")
    print("    Conversion therefore cannot be computed from this source, and inventing")
    print("    a number here would be the exact failure the rest of this repo argues")
    print("    against. Fix the vocabulary first, then measure.")
    print("=" * 84)
    return s


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", nargs="?", default=str(ROOT / "data" / "sample_pipeline.md"))
    ap.add_argument("--export-aggregate", metavar="OUT",
                    help="write counts only, safe to publish")
    args = ap.parse_args()

    path = Path(args.path)
    if not path.exists():
        raise SystemExit(f"no pipeline table at {path}")

    rows = parse(path)
    if not rows:
        raise SystemExit(f"parsed 0 rows from {path}. Expected a markdown table "
                         "whose first column is a row number.")
    stats = report(rows)

    if args.export_aggregate:
        out = Path(args.export_aggregate)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(stats, indent=2), encoding="utf-8")
        print(f"\n  aggregate written to {out}")
        print("  Counts only. No company names, roles, contacts or notes.")


if __name__ == "__main__":
    main()
