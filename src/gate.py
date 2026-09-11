"""The qualification gate.

Every GTM pipeline needs a stage that removes opportunities before anyone
spends effort on them. In a sales pipeline that is territory, budget and
segment. In this one it is work authorisation, which is the constraint that
decides whether an application can succeed at all no matter how good the fit.

Two design decisions carry the whole thing.

**Only a stated requirement is a wall.** Silence is not a rejection. A req
that says nothing about sponsorship is unknown, not hostile, and treating
silence as a wall removes most large employers from the pipeline for no
reason. So the gate separates STATED walls from ABSENT signal, and only the
first one blocks.

**A wall and a downgrade are different outcomes.** A citizenship requirement
is contractual and cannot be negotiated by a good application. A missing
sponsorship policy is a risk to be priced. Collapsing both into "skip" throws
away the second group, which is the largest group.

Every verdict carries the phrase that produced it, so a decision can be
audited later rather than argued about.

    python src/gate.py "some job description text"
    python src/gate.py --demo
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from enum import Enum


class Verdict(str, Enum):
    BLOCK = "block"        # stated, contractual, no application can succeed
    DOWNGRADE = "downgrade"  # allowed, but priced down
    PASS = "pass"          # nothing adverse stated


class Tier(str, Enum):
    T1_SPONSOR = "1-confirmed-sponsor"
    T2_EVERIFY = "2-no-sponsorship-but-everify"
    T3_NEITHER = "3-no-sponsorship-non-everify"
    T4_BLOCKED = "4-blocked"
    UNKNOWN = "unknown"


@dataclass
class Finding:
    rule: str
    verdict: Verdict
    evidence: str
    why: str


@dataclass
class GateResult:
    verdict: Verdict
    tier: Tier
    findings: list[Finding] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return self.verdict is Verdict.BLOCK

    def explain(self) -> str:
        lines = [f"verdict : {self.verdict.value}", f"tier    : {self.tier.value}"]
        for f in self.findings:
            lines.append(f"  [{f.verdict.value:<9}] {f.rule}")
            lines.append(f"              evidence: \"{f.evidence.strip()[:110]}\"")
            lines.append(f"              why     : {f.why}")
        for note in self.notes:
            lines.append(f"  note    : {note}")
        return "\n".join(lines)


# ---------------------------------------------------------------- the rules
# Each rule is (name, pattern, verdict, why). Order does not matter; the
# strongest verdict found wins, and every match is retained as evidence.

BLOCKING_RULES: list[tuple[str, str, str]] = [
    (
        "sponsorship-excludes-opt-cpt",
        r"(?:not|unable to|do(?:es)? not|cannot)[^.]{0,60}sponsor[^.]{0,120}"
        r"(?:\bOPT\b|\bCPT\b|F-?1|practical training)",
        "Names her actual status. This is the one sponsorship phrasing that is a "
        "genuine wall rather than a risk, and it is common on campus programmes.",
    ),
    (
        "us-citizenship-required",
        r"must be a (?:U\.?S\.?|United States) citizen"
        r"|U\.?S\.?\s+citizenship\s+(?:is\s+)?(?:required|mandatory)"
        r"|(?:only|exclusively)\s+U\.?S\.?\s+citizens",
        "Contractual rather than policy, usually flowing from a customer or "
        "programme requirement, so it cannot be argued past.",
    ),
    (
        "security-clearance-required",
        r"(?:active|current|existing)\s+(?:security\s+)?clearance"
        r"|(?:secret|top secret|TS/SCI|public trust)\s+clearance\s+(?:required|is required)"
        r"|ability to obtain (?:a )?(?:security )?clearance",
        "Clearance eligibility is generally restricted to citizens, so this is a "
        "citizenship wall stated indirectly.",
    ),
    (
        "export-control",
        r"\bITAR\b|\bEAR\b\s+(?:regulations|controlled)|export[- ]control(?:led|s)?\s+"
        r"(?:regulations|requirements|restrictions)|U\.?S\.?\s+person\s+(?:as defined|status)",
        "Export control status maps to citizenship or permanent residence.",
    ),
    (
        "restricted-posting-url",
        r"Private_Postings|Intern_Conversion_ONLY|Internal_ONLY|Restricted_Postings",
        "The URL says this req is not open to external applicants, so an "
        "application cannot be received regardless of fit.",
    ),
]

DOWNGRADE_RULES: list[tuple[str, str, str]] = [
    (
        "no-sponsorship-generic",
        r"(?:not|unable to|do(?:es)? not|will not|cannot)[^.]{0,40}"
        r"(?:provide|offer|sponsor)[^.]{0,40}(?:sponsorship|visa)"
        r"|no (?:visa )?sponsorship (?:is )?(?:available|provided|offered)",
        "Ordinary no-sponsorship language. NOT a wall on its own: it triggers an "
        "E-Verify check, because an E-Verify employer still supports a multi year "
        "runway. Retired as an auto-skip.",
    ),
    (
        "requires-existing-authorization",
        r"must (?:be |have )?(?:legally )?(?:authoriz|authoris)ed to work"
        r"|(?:current|existing|valid) work authorization (?:is )?required",
        "Satisfied by current status, so it is a note rather than a wall. Worth "
        "recording because it often sits beside a no-sponsorship line.",
    ),
]

POSITIVE_RULES: list[tuple[str, str, str]] = [
    (
        "sponsorship-offered",
        r"(?:will|do|does|can|happy to|able to)\s+sponsor"
        r"|sponsorship (?:is )?available|visa sponsorship (?:is )?(?:offered|provided)"
        r"|we sponsor",
        "Stated willingness to sponsor. Strongest possible signal from the req itself.",
    ),
    (
        "everify-employer",
        r"E-?Verify",
        "E-Verify enrolment is the criterion that matters when sponsorship is absent, "
        "because it evidences a compliant employer able to support a long runway.",
    ),
    (
        "cap-exempt-employer",
        r"\b(?:university|college)\b|academic medical cent(?:er|re)"
        r"|nonprofit research|non-profit research",
        "Cap-exempt employers skip the H1B lottery entirely, which materially "
        "improves the visa path.",
    ),
]

FRAUD_RULES: list[tuple[str, str, str]] = [
    (
        "off-domain-contact-email",
        r"(?:send|email|contact|forward)[^.]{0,60}"
        r"[\w.+-]+@(?!.*(?:gmail|outlook)\.)(?:his|aol|yahoo|hotmail|mail)\.com",
        "An off-domain contact address for a named brand is a recruitment scam tell. "
        "Never send identity documents, bank details or deposits.",
    ),
]


def _search(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.I)
    if not m:
        return None
    start = max(0, m.start() - 45)
    return text[start:m.end() + 45].replace("\n", " ")


# Words that invert the meaning of a positive phrase when they precede it.
NEGATION = re.compile(
    r"\b(?:no|not|non|never|without|unable|cannot|ineligible|excluded?)\b",
    re.I,
)


def _search_positive(text: str, pattern: str) -> str | None:
    """Find a positive signal, rejecting any match that is negated.

    Necessary because "sponsorship is available" is a substring of "NO
    sponsorship is available", and a naive positive match reads the second as
    the first. That exact bug promoted a no-sponsorship employer to the top
    tier during development, which is the most expensive direction to be
    wrong in: it spends a full application on a req that cannot convert.

    Python lookbehind must be fixed width, so the negation window is checked
    explicitly instead of being expressed in the pattern.
    """
    for m in re.finditer(pattern, text, re.I):
        window = text[max(0, m.start() - 40):m.start()]
        # Only the current clause matters. "We do not require a degree. We
        # sponsor." should still read as positive, so split on sentence marks
        # and inspect the tail.
        tail = re.split(r"[.;!?\n]", window)[-1]
        if NEGATION.search(tail):
            continue
        start = max(0, m.start() - 45)
        return text[start:m.end() + 45].replace("\n", " ")
    return None


def evaluate(jd_text: str, *, everify_confirmed: bool | None = None,
             lca_count: int | None = None, url: str = "") -> GateResult:
    """Run the gate over a job description.

    `everify_confirmed` and `lca_count` come from outside the JD, because the
    most important facts about an employer are usually not in its own posting.
    """
    haystack = f"{url}\n{jd_text or ''}"
    findings: list[Finding] = []
    notes: list[str] = []

    for name, pattern, why in FRAUD_RULES:
        if ev := _search(haystack, pattern):
            findings.append(Finding(name, Verdict.BLOCK, ev, why))

    for name, pattern, why in BLOCKING_RULES:
        if ev := _search(haystack, pattern):
            findings.append(Finding(name, Verdict.BLOCK, ev, why))

    for name, pattern, why in DOWNGRADE_RULES:
        if ev := _search(haystack, pattern):
            findings.append(Finding(name, Verdict.DOWNGRADE, ev, why))

    positives = []
    for name, pattern, why in POSITIVE_RULES:
        # E-Verify enrolment and cap-exempt status are facts about the entity
        # and are not inverted by the sentence around them. Offering
        # sponsorship is a claim, so it is.
        negatable = name not in ("everify-employer", "cap-exempt-employer")
        finder = _search_positive if negatable else _search
        if ev := finder(haystack, pattern):
            positives.append(Finding(name, Verdict.PASS, ev, why))
    findings.extend(positives)

    blocked = [f for f in findings if f.verdict is Verdict.BLOCK]
    downgrades = [f for f in findings if f.verdict is Verdict.DOWNGRADE]
    positive_names = {f.rule for f in positives}

    # ---- verdict
    if blocked:
        verdict, tier = Verdict.BLOCK, Tier.T4_BLOCKED
    elif lca_count:
        verdict = Verdict.PASS
        tier = Tier.T1_SPONSOR
        notes.append(f"{lca_count} LCA filing(s) on record, which outweighs "
                     f"silence or boilerplate in the posting")
    elif "sponsorship-offered" in positive_names:
        verdict, tier = Verdict.PASS, Tier.T1_SPONSOR
    elif downgrades:
        if everify_confirmed or "everify-employer" in positive_names:
            verdict, tier = Verdict.DOWNGRADE, Tier.T2_EVERIFY
            notes.append("No sponsorship, but E-Verify supports a multi year runway, "
                         "so this is priced down rather than removed.")
        elif everify_confirmed is False:
            verdict, tier = Verdict.DOWNGRADE, Tier.T3_NEITHER
            notes.append("No sponsorship and not E-Verify. Lowest tier that is still "
                         "in the pipeline. A downgrade, deliberately not a skip.")
        else:
            verdict, tier = Verdict.DOWNGRADE, Tier.UNKNOWN
            notes.append("No sponsorship stated and E-Verify status unchecked. "
                         "Resolve E-Verify before deciding; do not infer it.")
    else:
        verdict, tier = Verdict.PASS, Tier.UNKNOWN
        notes.append("Nothing adverse stated. Silence is NOT a rejection, so this "
                     "stays in the pipeline pending an employer level check.")

    if "cap-exempt-employer" in positive_names and tier is not Tier.T4_BLOCKED:
        notes.append("Possible cap-exempt employer, which skips the lottery.")

    return GateResult(verdict=verdict, tier=tier, findings=findings, notes=notes)


DEMO = [
    ("stated OPT wall",
     "This role is not eligible for visa sponsorship, including OPT or CPT."),
    ("citizenship wall",
     "Applicants must be a U.S. citizen due to federal contract requirements."),
    ("clearance wall",
     "An active Secret clearance is required on day one."),
    ("ordinary no-sponsorship",
     "We are unable to provide visa sponsorship for this position at this time."),
    ("no-sponsorship at an E-Verify employer",
     "No visa sponsorship is available. This employer participates in E-Verify."),
    ("explicit sponsor",
     "We will sponsor qualified candidates and support relocation."),
    ("silence",
     "You will build data pipelines and dashboards for a growing analytics team."),
    ("scam tell",
     "To proceed, email your documents to recruiting.team@his.com immediately."),
]


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] != "--demo":
        print(evaluate(" ".join(sys.argv[1:])).explain())
        return

    print("=" * 78)
    print("  QUALIFICATION GATE".center(78))
    print("=" * 78)
    for label, text in DEMO:
        kwargs = {}
        if label == "no-sponsorship at an E-Verify employer":
            kwargs["everify_confirmed"] = True
        result = evaluate(text, **kwargs)
        print(f"\n  {label}")
        print(f"  {'-' * len(label)}")
        for line in result.explain().split("\n"):
            print("  " + line)
    print("\n" + "=" * 78)


if __name__ == "__main__":
    main()
