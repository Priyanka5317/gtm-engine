/* gate-engine.js - the qualification gate, ported from src/gate.py.
 *
 * WHY THIS EXISTS TWICE
 *
 * The browser demo has to run with no server and no key, so it cannot call
 * the Python. A second implementation is a liability unless something proves
 * the two agree, so tests/parity_check.mjs runs every gate case through both
 * and fails if a single verdict, tier or rule set differs. The demo is
 * therefore evidence rather than decoration.
 *
 * Ported line for line. The regexes are byte-identical to the Python ones;
 * where Python and JS differ (finditer vs matchAll, re.I vs /i) the
 * behaviour is matched, not approximated.
 */

export const Verdict = { BLOCK: 'block', DOWNGRADE: 'downgrade', PASS: 'pass' };

export const Tier = {
  T1_SPONSOR: '1-confirmed-sponsor',
  T2_EVERIFY: '2-no-sponsorship-but-everify',
  T3_NEITHER: '3-no-sponsorship-non-everify',
  T4_BLOCKED: '4-blocked',
  UNKNOWN: 'unknown',
};

export const BLOCKING_RULES = [
  [
    'sponsorship-excludes-opt-cpt',
    /(?:not|unable to|do(?:es)? not|cannot)[^.]{0,60}sponsor[^.]{0,120}(?:\bOPT\b|\bCPT\b|F-?1|practical training)/i,
    'Names her actual status. This is the one sponsorship phrasing that is a ' +
      'genuine wall rather than a risk, and it is common on campus programmes.',
  ],
  [
    'us-citizenship-required',
    /must be a (?:U\.?S\.?|United States) citizen|U\.?S\.?\s+citizenship\s+(?:is\s+)?(?:required|mandatory)|(?:only|exclusively)\s+U\.?S\.?\s+citizens/i,
    'Contractual rather than policy, usually flowing from a customer or ' +
      'programme requirement, so it cannot be argued past.',
  ],
  [
    'security-clearance-required',
    /(?:active|current|existing)\s+(?:security\s+)?clearance|(?:secret|top secret|TS\/SCI|public trust)\s+clearance\s+(?:required|is required)|ability to obtain (?:a )?(?:security )?clearance/i,
    'Clearance eligibility is generally restricted to citizens, so this is a ' +
      'citizenship wall stated indirectly.',
  ],
  [
    'export-control',
    /\bITAR\b|\bEAR\b\s+(?:regulations|controlled)|export[- ]control(?:led|s)?\s+(?:regulations|requirements|restrictions)|U\.?S\.?\s+person\s+(?:as defined|status)/i,
    'Export control status maps to citizenship or permanent residence.',
  ],
  [
    'restricted-posting-url',
    /Private_Postings|Intern_Conversion_ONLY|Internal_ONLY|Restricted_Postings/i,
    'The URL says this req is not open to external applicants, so an ' +
      'application cannot be received regardless of fit.',
  ],
];

export const DOWNGRADE_RULES = [
  [
    'no-sponsorship-generic',
    /(?:not|unable to|do(?:es)? not|will not|cannot)[^.]{0,40}(?:provide|offer|sponsor)[^.]{0,40}(?:sponsorship|visa)|no (?:visa )?sponsorship (?:is )?(?:available|provided|offered)/i,
    'Ordinary no-sponsorship language. NOT a wall on its own: it triggers an ' +
      'E-Verify check, because an E-Verify employer still supports a multi year ' +
      'runway. Retired as an auto-skip.',
  ],
  [
    'requires-existing-authorization',
    /must (?:be |have )?(?:legally )?(?:authoriz|authoris)ed to work|(?:current|existing|valid) work authorization (?:is )?required/i,
    'Satisfied by current status, so it is a note rather than a wall. Worth ' +
      'recording because it often sits beside a no-sponsorship line.',
  ],
];

export const POSITIVE_RULES = [
  [
    'sponsorship-offered',
    /(?:will|do|does|can|happy to|able to)\s+sponsor|sponsorship (?:is )?available|visa sponsorship (?:is )?(?:offered|provided)|we sponsor/i,
    'Stated willingness to sponsor. Strongest possible signal from the req itself.',
  ],
  [
    'everify-employer',
    /E-?Verify/i,
    'E-Verify enrolment is the criterion that matters when sponsorship is absent, ' +
      'because it evidences a compliant employer able to support a long runway.',
  ],
  [
    'cap-exempt-employer',
    /\b(?:university|college)\b|academic medical cent(?:er|re)|nonprofit research|non-profit research/i,
    'Cap-exempt employers skip the H1B lottery entirely, which materially ' +
      'improves the visa path.',
  ],
];

export const FRAUD_RULES = [
  [
    'off-domain-contact-email',
    /(?:send|email|contact|forward)[^.]{0,60}[\w.+-]+@(?!.*(?:gmail|outlook)\.)(?:his|aol|yahoo|hotmail|mail)\.com/i,
    'An off-domain contact address for a named brand is a recruitment scam tell. ' +
      'Never send identity documents, bank details or deposits.',
  ],
];

/* Words that invert the meaning of a positive phrase when they precede it. */
const NEGATION = /\b(?:no|not|non|never|without|unable|cannot|ineligible|excluded?)\b/i;

/** Mirror of Python's _search: first match, with 45 chars of context each side. */
function search(text, pattern) {
  const m = pattern.exec(text);
  if (!m) return null;
  const start = Math.max(0, m.index - 45);
  return text.slice(start, m.index + m[0].length + 45).replace(/\n/g, ' ');
}

/** Mirror of Python's _search_positive: reject any match negated in its own clause.
 *
 * "sponsorship is available" is a substring of "NO sponsorship is available",
 * and a naive match reads the second as the first. That bug promoted a
 * no-sponsorship employer to the top tier during development.
 */
function searchPositive(text, pattern) {
  const g = new RegExp(pattern.source, pattern.flags.includes('g') ? pattern.flags : pattern.flags + 'g');
  for (const m of text.matchAll(g)) {
    const window = text.slice(Math.max(0, m.index - 40), m.index);
    /* Only the current clause matters. "We do not require a degree. We
     * sponsor." should still read as positive. */
    const parts = window.split(/[.;!?\n]/);
    const tail = parts[parts.length - 1];
    if (NEGATION.test(tail)) continue;
    const start = Math.max(0, m.index - 45);
    return text.slice(start, m.index + m[0].length + 45).replace(/\n/g, ' ');
  }
  return null;
}

/**
 * Run the gate over a job description.
 *
 * everifyConfirmed and lcaCount come from outside the JD, because the most
 * important facts about an employer are usually not in its own posting.
 * Pass everifyConfirmed as null/undefined for "unchecked" - false means
 * "checked, and not enrolled", which is a different tier.
 */
export function evaluate(jdText, { everifyConfirmed = null, lcaCount = null, url = '' } = {}) {
  const haystack = `${url}\n${jdText || ''}`;
  const findings = [];
  const notes = [];

  for (const [name, pattern, why] of FRAUD_RULES) {
    const ev = search(haystack, pattern);
    if (ev) findings.push({ rule: name, verdict: Verdict.BLOCK, evidence: ev, why });
  }
  for (const [name, pattern, why] of BLOCKING_RULES) {
    const ev = search(haystack, pattern);
    if (ev) findings.push({ rule: name, verdict: Verdict.BLOCK, evidence: ev, why });
  }
  for (const [name, pattern, why] of DOWNGRADE_RULES) {
    const ev = search(haystack, pattern);
    if (ev) findings.push({ rule: name, verdict: Verdict.DOWNGRADE, evidence: ev, why });
  }

  const positives = [];
  for (const [name, pattern, why] of POSITIVE_RULES) {
    /* E-Verify enrolment and cap-exempt status are facts about the entity and
     * are not inverted by the sentence around them. Offering sponsorship is a
     * claim, so it is. */
    const negatable = name !== 'everify-employer' && name !== 'cap-exempt-employer';
    const ev = negatable ? searchPositive(haystack, pattern) : search(haystack, pattern);
    if (ev) positives.push({ rule: name, verdict: Verdict.PASS, evidence: ev, why });
  }
  findings.push(...positives);

  const blocked = findings.filter((f) => f.verdict === Verdict.BLOCK);
  const downgrades = findings.filter((f) => f.verdict === Verdict.DOWNGRADE);
  const positiveNames = new Set(positives.map((f) => f.rule));

  let verdict;
  let tier;
  if (blocked.length) {
    verdict = Verdict.BLOCK;
    tier = Tier.T4_BLOCKED;
  } else if (lcaCount) {
    verdict = Verdict.PASS;
    tier = Tier.T1_SPONSOR;
    notes.push(`${lcaCount} LCA filing(s) on record, which outweighs silence or boilerplate in the posting`);
  } else if (positiveNames.has('sponsorship-offered')) {
    verdict = Verdict.PASS;
    tier = Tier.T1_SPONSOR;
  } else if (downgrades.length) {
    if (everifyConfirmed || positiveNames.has('everify-employer')) {
      verdict = Verdict.DOWNGRADE;
      tier = Tier.T2_EVERIFY;
      notes.push('No sponsorship, but E-Verify supports a multi year runway, so this is priced down rather than removed.');
    } else if (everifyConfirmed === false) {
      verdict = Verdict.DOWNGRADE;
      tier = Tier.T3_NEITHER;
      notes.push('No sponsorship and not E-Verify. Lowest tier that is still in the pipeline. A downgrade, deliberately not a skip.');
    } else {
      verdict = Verdict.DOWNGRADE;
      tier = Tier.UNKNOWN;
      notes.push('No sponsorship stated and E-Verify status unchecked. Resolve E-Verify before deciding; do not infer it.');
    }
  } else {
    verdict = Verdict.PASS;
    tier = Tier.UNKNOWN;
    notes.push('Nothing adverse stated. Silence is NOT a rejection, so this stays in the pipeline pending an employer level check.');
  }

  if (positiveNames.has('cap-exempt-employer') && tier !== Tier.T4_BLOCKED) {
    notes.push('Possible cap-exempt employer, which skips the lottery.');
  }

  return { verdict, tier, findings, notes };
}

/* The same eight cases src/gate.py ships as --demo, so the browser and the
 * CLI demonstrate identical behaviour. */
export const DEMO = [
  ['stated OPT wall', 'This role is not eligible for visa sponsorship, including OPT or CPT.', {}],
  ['citizenship wall', 'Applicants must be a U.S. citizen due to federal contract requirements.', {}],
  ['clearance wall', 'An active Secret clearance is required on day one.', {}],
  ['ordinary no-sponsorship', 'We are unable to provide visa sponsorship for this position at this time.', {}],
  ['no-sponsorship at an E-Verify employer', 'No visa sponsorship is available. This employer participates in E-Verify.', { everifyConfirmed: true }],
  ['explicit sponsor', 'We will sponsor qualified candidates and support relocation.', {}],
  ['silence', 'You will build data pipelines and dashboards for a growing analytics team.', {}],
  ['scam tell', 'To proceed, email your documents to recruiting.team@his.com immediately.', {}],
];
