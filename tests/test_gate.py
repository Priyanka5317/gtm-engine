"""Gate tests. No network, so these run in milliseconds.

The cases are chosen around the boundary rather than the middle, because the
middle was never the problem. Two in particular exist because the gate got
them wrong during development:

  negated positive    "NO sponsorship is available" contains "sponsorship is
                      available", and a naive positive match promoted a
                      no-sponsorship employer to the top tier.

  clause scoping      "We do not require a degree. We sponsor." must stay
                      positive. Over-correcting the bug above by scanning a
                      flat window would have broken this one.

    python tests/test_gate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from gate import Tier, Verdict, evaluate  # noqa: E402

CASES = [
    # label, jd text, kwargs, expected verdict, expected tier
    ("stated OPT wall",
     "This role is not eligible for visa sponsorship, including OPT or CPT.",
     {}, Verdict.BLOCK, Tier.T4_BLOCKED),

    ("citizenship wall",
     "Applicants must be a U.S. citizen due to federal contract requirements.",
     {}, Verdict.BLOCK, Tier.T4_BLOCKED),

    ("clearance wall",
     "An active Secret clearance is required on day one.",
     {}, Verdict.BLOCK, Tier.T4_BLOCKED),

    ("export control wall",
     "This position is subject to ITAR and requires U.S. person status.",
     {}, Verdict.BLOCK, Tier.T4_BLOCKED),

    ("restricted posting url",
     "",
     {"url": "https://x.wd1.myworkdayjobs.com/Private_Postings/job/123"},
     Verdict.BLOCK, Tier.T4_BLOCKED),

    ("scam tell, off domain contact",
     "To proceed, email your documents to recruiting.team@his.com immediately.",
     {}, Verdict.BLOCK, Tier.T4_BLOCKED),

    ("explicit sponsor",
     "We will sponsor qualified candidates and support relocation.",
     {}, Verdict.PASS, Tier.T1_SPONSOR),

    ("LCA evidence outweighs a silent posting",
     "You will build dashboards for the analytics team.",
     {"lca_count": 137}, Verdict.PASS, Tier.T1_SPONSOR),

    # The regression that mattered most.
    ("negated positive stays a downgrade",
     "No visa sponsorship is available. This employer participates in E-Verify.",
     {"everify_confirmed": True}, Verdict.DOWNGRADE, Tier.T2_EVERIFY),

    # The over-correction guard.
    ("negation in a PRIOR clause does not suppress a positive",
     "We do not require a degree. We sponsor qualified candidates.",
     {}, Verdict.PASS, Tier.T1_SPONSOR),

    ("no sponsorship, not E-Verify, still in the pipeline",
     "We cannot offer visa sponsorship for this role.",
     {"everify_confirmed": False}, Verdict.DOWNGRADE, Tier.T3_NEITHER),

    ("no sponsorship, E-Verify unchecked, resolve before deciding",
     "We are unable to provide visa sponsorship at this time.",
     {}, Verdict.DOWNGRADE, Tier.UNKNOWN),

    # Silence must not be treated as hostile. This is the rule that keeps
    # most large employers in the pipeline.
    ("silence is not a rejection",
     "You will build data pipelines and dashboards for a growing team.",
     {}, Verdict.PASS, Tier.UNKNOWN),

    ("cap exempt is noted, not required",
     "The University seeks a data analyst for its research computing group.",
     {}, Verdict.PASS, Tier.UNKNOWN),
]


def main() -> int:
    print("=" * 92)
    print("  GATE TESTS".center(92))
    print("=" * 92)

    failed = 0
    for label, text, kwargs, want_verdict, want_tier in CASES:
        result = evaluate(text, **kwargs)
        ok = result.verdict is want_verdict and result.tier is want_tier
        if not ok:
            failed += 1
        print(f"  {'PASS' if ok else 'FAIL'}  {label:<52} "
              f"{result.verdict.value:<10} {result.tier.value}")
        if not ok:
            print(f"        expected {want_verdict.value} / {want_tier.value}")
            print(f"        rules fired: {[f.rule for f in result.findings]}")

    # A blocked req must always carry the phrase that blocked it. A verdict
    # with no evidence is not auditable, and an unauditable gate is one
    # nobody will trust enough to leave switched on.
    for label, text, kwargs, want_verdict, _ in CASES:
        if want_verdict is not Verdict.BLOCK:
            continue
        result = evaluate(text, **kwargs)
        blockers = [f for f in result.findings if f.verdict is Verdict.BLOCK]
        if not blockers or not all(f.evidence for f in blockers):
            print(f"  FAIL  blocked with no evidence attached: {label}")
            failed += 1

    print("-" * 92)
    total = len(CASES)
    print(f"  {total - failed}/{total} cases correct"
          + ("" if failed else ", plus every block carries its evidence"))
    print("=" * 92)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
