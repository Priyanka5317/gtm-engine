"""Live smoke test for every adapter.

These hit the real vendors. No mocks, because the thing most likely to break
is not my parsing, it is a vendor quietly changing a field name, and a mock
would hide exactly that.

    python tests/test_live.py            every adapter that has a fixture
    python tests/test_live.py greenhouse ashby      only these

A closed req is reported as SKIP, not FAIL. Job postings expire, so a fixture
going 404 says nothing about whether the adapter works. That distinction is
the whole reason `PostingClosed` exists.

Exit code is 0 only if nothing FAILED.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from adapters import FetchError, PostingClosed, detect, fetch, registry  # noqa: E402

# Every URL below was verified live on 2026-09-10. Several came straight out
# of a real 533 row pipeline, which is why the set skews to roles a data
# candidate would actually have looked at.
FIXTURES: list[tuple[str, str, str]] = [
    # platform, url, what we expect to be true
    ("greenhouse",
     "https://job-boards.greenhouse.io/embed/job_app?for=applovin&token=4705263006",
     "comp band in the body, 120k to 180k"),
    ("greenhouse",
     "https://job-boards.greenhouse.io/bertramcapitalmanagement/jobs/8789855002",
     "from the real pipeline"),
    ("ashby",
     "https://jobs.ashbyhq.com/planhat/ae6a1b96-f290-4915-86c9-0d3c75a0dcb3",
     "compensation object empty, band is in the body, 170k to 250k"),
    ("ashby",
     "https://jobs.ashbyhq.com/rivianvw.tech/988d2bf1-caf8-4a16-892d-bb726dc832ec",
     "org slug contains a dot"),
    ("lever",
     "https://jobs.lever.co/field-ai/b600a2ab-cff1-4b8f-9940-4bd851c05e37",
     "from the real pipeline"),
    ("lever",
     "https://jobs.lever.co/vitana-pediatric/80e4fc09-ab97-4a21-ba8e-9e41ff17d25e/apply",
     "url has an /apply suffix after the id"),
    ("smartrecruiters",
     "https://jobs.smartrecruiters.com/ServiceNow/744000138174539",
     "band in the body, 139k to 216k"),
    ("smartrecruiters",
     "https://jobs.smartrecruiters.com/CapTechConsulting/744000115233343",
     "from the real pipeline"),
    ("workday",
     "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/job/"
     "US-CA-Santa-Clara/Principal-Data-Scientist---Cloud-Gaming-and-AI_JR2019886",
     "cxs needs the whole path after /job/, plus multi location"),
    ("workday",
     "https://cox.wd1.myworkdayjobs.com/Cox_External_Career_Site_1/job/"
     "Atlanta-GA/Senior-Manager--Thought-Leadership--B2B-Retail_R202682093",
     "band in the body, 101k to 169k"),
    ("oracle",
     "https://eewl.fa.us6.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/job/10993",
     "detail resource with the id as a PATH param, pod host"),
    ("oracle",
     "https://icfcjb.fa.ocs.oraclecloud.com/hcmUI/CandidateExperience/en/sites/"
     "Aerospace/job/120618",
     "different pod, different region segment"),
    ("dayforce",
     "https://jobs.dayforcehcm.com/en-US/ontrac/CANDIDATEPORTAL/jobs/62529",
     "req id 9004 differs from the url posting id 62529"),
    ("dayforce",
     "https://us242.dayforcehcm.com/CandidatePortal/en-US/asrcfh/SITE/"
     "CANDIDATEPORTAL/Posting/View/4396",
     "the older pod hosted url shape"),
    ("rippling",
     "https://ats.rippling.com/rippling/jobs/0bcf2aae-8297-4c7c-8b1f-a6ed33c85e9e",
     "description arrives as a dict of html fragments, not a string"),
    ("icims",
     "https://tuccareers-touro.icims.com/jobs/13268/data-analyst/job",
     "expected to be CLOSED, proves 410 is classified not crashed"),
    ("eightfold",
     "https://johndeere.eightfold.ai/careers/job/137482523381",
     "path-form url: /careers/job/{pid}, no domain param, so it is derived"),
    ("eightfold",
     "https://qualcomm.eightfold.ai/careers/job/446721016271",
     "second tenant, confirms the tenant is not hardcoded anywhere"),
    ("workable",
     "https://apply.workable.com/accora/j/4A8BA0A44E/apply",
     "url has an /apply suffix; account slug and shortcode both come from the path"),
    ("workable",
     "https://apply.workable.com/credence/j/BAD39E80E0/apply",
     "second account, and the widget api serves the body without a key"),
    ("taleo",
     "https://massanf.taleo.net/careersection/ex/jobdetail.ftl?job=1072298",
     "expected to be CLOSED, and Taleo serves that as HTTP 200 - only the body "
     "says so, which is why the adapter asserts on the body"),
]

# Implemented against a documented endpoint but with no live fixture pinned
# here. Named explicitly rather than left to be inferred from the pass count,
# because "13 platforms supported" and "13 platforms verified" are different
# claims and only one of them is true.
UNVERIFIED_NOTES = {
    "phenom": "matcher is generic by design, so any pinned fixture would be one vendor",
}

# Kept as a record, because "we could not verify it" and "we verified it and
# here is what we learned" are different claims and the first one aged badly:
#   workable   the earlier note said no open req could be found. Two were,
#              from the same 21,000 row feed the pipeline already had.
#   eightfold  the earlier note said a tenant plus pid pair was needed. The
#              pair was never the problem - the adapter only matched
#              ?pid={pid}, while every real share link is /careers/job/{pid}.
#   taleo      the earlier note blamed TLS. massanf answers fine now, and the
#              real finding is that an expired req arrives as HTTP 200 with a
#              "no longer available" body, which the adapter used to report as
#              its own failure.


def run(only: set[str] | None = None) -> int:
    platforms = sorted({r["platform"] for r in registry()})
    covered = sorted({p for p, _, _ in FIXTURES})
    uncovered = [p for p in platforms if p not in covered]

    rows = FIXTURES if not only else [f for f in FIXTURES if f[0] in only]

    print("=" * 96)
    print("  LIVE ADAPTER SMOKE TEST".center(96))
    print("=" * 96)

    passed = failed = skipped = 0

    for platform, url, note in rows:
        found = detect(url)
        label = f"{platform:<16}"

        if not found or found[0] != platform:
            print(f"  FAIL {label} routing: detect() said {found} for this url")
            failed += 1
            continue

        try:
            job = fetch(url)
        except PostingClosed as err:
            print(f"  SKIP {label} req closed (HTTP {err.status}), adapter untested here")
            print(f"       {note}")
            skipped += 1
            continue
        except FetchError as err:
            print(f"  FAIL {label} {str(err)[:74]}")
            failed += 1
            continue

        problems = []
        if not job.title:
            problems.append("no title")
        if not job.has_description:
            problems.append(f"description only {len(job.description or '')} chars")

        if problems:
            print(f"  FAIL {label} {', '.join(problems)}")
            failed += 1
            continue

        comp = (
            f"{job.comp_min:,} to {job.comp_max:,}"
            if job.comp_min and job.comp_max else
            f"{job.comp_min:,}+" if job.comp_min else "no band"
        )
        print(f"  PASS {label} {(job.title or '')[:40]:<40} "
              f"jd={len(job.description):>5}  {comp}")
        if job.unresolved_fields:
            print(f"       unresolved: {', '.join(job.unresolved_fields)}")
        passed += 1
        time.sleep(0.4)      # be a polite client

    print("-" * 96)
    print(f"  {passed} passed, {failed} failed, {skipped} skipped (closed reqs)")
    print(f"  platforms with a live fixture : {len(covered)}/{len(platforms)}  "
          f"({', '.join(covered)})")
    if uncovered:
        print(f"  NOT covered by a live fixture : {', '.join(uncovered)}")
        for name in uncovered:
            print(f"       {name:<16} {UNVERIFIED_NOTES.get(name, 'no note')}")
    print("=" * 96)
    return 1 if failed else 0


if __name__ == "__main__":
    only = set(sys.argv[1:]) or None
    raise SystemExit(run(only))
