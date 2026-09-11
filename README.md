# GTM Engine

A go-to-market pipeline engine, pointed at a real one.

Source, enrich, qualify, route, measure. The pipeline it runs on is **534 real opportunities across 462 companies over six months**, sourced from **21 different applicant tracking systems**. The "customers" are employers.

Python 3.11, **no dependencies, no API keys, no accounts**. Every number below came from a live run and reproduces.

---

## Does it work?

### Adapters, tested against the live vendors

```
python tests/test_live.py

14 passed, 0 failed, 2 skipped (closed reqs)
platforms with a live fixture : 9/13
NOT covered by a live fixture : eightfold, phenom, taleo, workable
```

No mocks. The thing most likely to break is not the parsing, it is a vendor quietly renaming a field, and a mock hides exactly that.

**13 adapters implemented, 9 verified live.** Those are different claims and the test prints both, along with why each unverified one is unverified.

### The gate

```
python tests/test_gate.py

14/14 cases correct, plus every block carries its evidence
```

### The funnel, over the real pipeline

```
rows                 534        distinct companies   462
months covered       6 (2026-04 to 2026-09)
ATS platforms        21

screened_out         108        removed before any effort was spent
evaluated            305
pack_built            78
applied               22
```

---

## The three integration patterns

21 tracking systems look like 21 problems. They are three, and this classification is the actual asset. It is what makes the eleventh integration take twenty minutes instead of an afternoon.

| Family | How the data arrives | Adapters |
|---|---|---|
| **1. Public JSON board API** | documented or near enough, clean JSON | greenhouse, ashby, lever, smartrecruiters, workable, rippling |
| **2. JSON embedded in the HTML** | page is a shell, payload is in a script tag | dayforce, taleo, phenom |
| **3. Undocumented internal API** | needs an exact path, header, method or flag | workday, icims, oracle, eightfold |

**Family 1** gives you a whole-company scan for free: request the board without a req id and you get every open role.

**Family 2's** tell is a page that looks empty to a text extractor but weighs 200KB. If the bytes are there and the text is not, stop hunting for an endpoint and read the script tags.

**Family 3 is where the money is, and where the trap is.** These vendors serve a client-rendered shell, so fetching the posting URL returns a page with no job description **and HTTP 200**. A naive scraper records a success and stores an empty JD. On this family a 200 means nothing, so `has_description` asserts a real body before anything is trusted.

### What each Family 3 vendor needed

- **Workday**: the CXS endpoint wants the **whole path** after `/job/`, not the trailing requisition id. Passing the bare id 404s, which is the mistake that makes people conclude Workday is unscrapable.
- **iCIMS**: desktop returns an empty shell. `?mobile=true&needsRedirect=false` **plus a mobile user agent** returns the posting. Session tokens in a shared link (`eem=`, `code=`) must be stripped first.
- **Oracle**: the documented `ByRequisitionId` finder returns 400. What works is the detail resource with the id as a **path** parameter, against the pod host rather than a vanity careers domain.
- **Dayforce**: the description is nested under `jobPostingContent`, and the compensation band lives **only** inside `jobPostingAttributes[]`.

---

## Two bugs worth reading about

Both were caught by the tests, and both are the kind that ship silently.

### 1. A negated positive promoted a no-sponsorship employer to the top tier

The gate looked for `sponsorship is available` to detect a sponsoring employer. That phrase is a **substring of "NO sponsorship is available."** So a req that explicitly refused sponsorship was classified as a confirmed sponsor.

That is the most expensive direction to be wrong in, because it spends a full application on a req that cannot convert.

The fix is not a longer pattern. Python lookbehind must be fixed width, so the negation window is checked explicitly, **scoped to the current clause**. The scoping matters: over-correcting with a flat window would break `"We do not require a degree. We sponsor qualified candidates."`, which must stay positive. Both cases are now pinned tests.

### 2. Dayforce returned a title and no description

The first adapter read `job["description"]`, which does not exist. It returned a posting with a real title, a real req id, and an **empty body**, and reported success. A confident blank.

The live test now asserts a description of at least 400 characters before any adapter passes, because that failure is invisible from the outside and poisons everything downstream.

Also confirmed while fixing it: `jobReqId` is genuinely **not** the id in the URL. On a live posting the URL id was `62529` and the requisition id was `9004`. Matching an internal tracker on the URL id fails silently.

---

## The qualification gate

Every GTM pipeline needs a stage that removes opportunities before anyone spends effort. In sales that is territory, budget and segment. Here it is work authorisation, which decides whether an application can succeed **at all**, regardless of fit.

**108 of 534 opportunities were screened out before a single artifact was built.** That is the gate paying for itself.

Two design decisions carry the whole thing:

**Only a stated requirement is a wall.** Silence is not a rejection. A posting that says nothing about sponsorship is *unknown*, not hostile, and treating silence as a wall removes most large employers for no reason. So `BLOCK` and `PASS` are separated from `UNKNOWN`, and only a stated requirement blocks.

**A wall and a downgrade are different outcomes.** A citizenship requirement is contractual and no application can argue past it. A missing sponsorship policy is a risk to be priced. Collapsing both into "skip" throws away the larger group.

```
python src/gate.py --demo
```

Every verdict carries the phrase that produced it. A gate nobody can audit is a gate nobody leaves switched on.

| Tier | Meaning |
|---|---|
| 1 | confirmed sponsor, or filings on record |
| 2 | no sponsorship stated, but E-Verify enrolled |
| 3 | neither. A downgrade, deliberately not a skip |
| 4 | blocked by a stated, contractual requirement |
| unknown | nothing adverse stated. Resolve, do not infer |

External evidence outranks the posting's own text: a company with filings on record is Tier 1 even if its JD says nothing, because **what an employer has actually done beats what its boilerplate says.**

---

## Stage vocabulary drift, the finding I did not expect

The source pipeline has the exact defect this engine exists to argue about.

```
distinct status literals in the data : 107
canonical stages they should map to  :   6
rows mapped by the table             : 96.1%
```

**107 distinct status values for what should be six stages**, in two languages, accumulated over six months. Not sloppiness. This is what every CRM looks like after six months without an owned vocabulary, and it is why "define the stage once" is an engineering position rather than a platitude.

The funnel **never silently normalises.** Anything it cannot map goes in an `unmapped` bucket and gets printed, because a funnel that hides its own coverage is the thing being argued against.

### What this engine refuses to tell you

Submission is recorded inconsistently, so **the applied count is a floor, not a total.** Interview outcomes are not in the table at all.

So conversion **cannot be computed from this source**, and the engine says so instead of printing a number. Any headline rate you have seen for this pipeline is self-reported and not derived here. Fix the vocabulary first, then measure.

That refusal is the point. An engine that invents a conversion rate to fill a slot is worse than one that leaves it empty.

---

## Privacy

The real table holds company names, recruiter identities and private notes. **It is not in this repo.** Point the funnel at a local file.

```bash
python src/funnel.py                                   # bundled synthetic sample
python src/funnel.py path/to/pipeline.md
python src/funnel.py path/to/pipeline.md --export-aggregate out/funnel.json
```

`--export-aggregate` writes **counts only**, which is the artifact that is safe to publish. The engine ships; the data does not.

---

## Run it

```bash
python tests/test_live.py            # every adapter, against the live vendors
python tests/test_live.py workday    # just one
python tests/test_gate.py            # gate, no network
python src/gate.py --demo            # gate verdicts with evidence
python src/funnel.py                 # funnel over the sample
```

Fetch any posting from any supported vendor:

```python
import sys; sys.path.insert(0, "src")
from adapters import fetch

job = fetch("https://job-boards.greenhouse.io/embed/job_app?for=applovin&token=4705263006")
print(job.title, job.comp_min, job.comp_max)     # Data Scientist, Analytics 120000 180000
print(job.unresolved_fields)                     # what could not be resolved
```

## Layout

```
src/adapters/base.py         JobPosting, http, registry, PostingClosed
src/adapters/json_board.py   family 1, six vendors
src/adapters/embedded.py     family 2, three vendors
src/adapters/internal.py     family 3, four vendors
src/gate.py                  qualification gate, evidence attached to every verdict
src/funnel.py                pipeline analytics, declares its own coverage
tests/test_live.py           live vendor smoke test
tests/test_gate.py           gate boundary cases
data/sample_pipeline.md      synthetic, so the repo runs standalone
```

A closed req raises `PostingClosed`, not `FetchError`. Postings expire constantly, and if a closed req raises the same error as a broken integration you will spend an afternoon debugging a working adapter.
