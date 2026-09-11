/* parity_check.mjs - proves the browser gate and the Python gate agree.
 *
 * The demo in web/ cannot call src/gate.py, so the rules exist twice. Two
 * implementations of the same logic drift silently, and a demo that quietly
 * disagrees with the engine is worse than no demo: it invites a hiring
 * manager to trust a number the repo cannot reproduce.
 *
 * So this test takes the cases from tests/test_gate.py - the SAME list the
 * Python suite asserts against, never a copy - runs each through both
 * implementations, and fails on any difference in verdict, tier or the set
 * of rules that fired.
 *
 *     node tests/parity_check.mjs
 */

import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { evaluate } from '../web/gate-engine.js';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));

/* Ask the Python side for both the cases and its own verdicts, so the case
 * list has exactly one definition (tests/test_gate.py) for both languages. */
const DUMP = `
import json, sys
from pathlib import Path
root = Path(${JSON.stringify(ROOT)})
sys.path.insert(0, str(root / "src"))
sys.path.insert(0, str(root / "tests"))
from test_gate import CASES
from gate import evaluate
out = []
for label, text, kwargs, _ev, _et in CASES:
    r = evaluate(text, **kwargs)
    out.append({
        "label": label,
        "text": text,
        "kwargs": kwargs,
        "verdict": r.verdict.value,
        "tier": r.tier.value,
        "rules": sorted(f.rule for f in r.findings),
    })
print(json.dumps(out))
`;

function pythonResults() {
  for (const exe of ['python', 'python3']) {
    try {
      const raw = execFileSync(exe, ['-c', DUMP], { cwd: ROOT, encoding: 'utf8' });
      return JSON.parse(raw);
    } catch (err) {
      if (err && err.code === 'ENOENT') continue;
      throw err;
    }
  }
  throw new Error('no python interpreter found on PATH');
}

/* Python uses snake_case keyword arguments; the JS port uses camelCase. */
function toJsOpts(kwargs) {
  const o = {};
  if ('everify_confirmed' in kwargs) o.everifyConfirmed = kwargs.everify_confirmed;
  if ('lca_count' in kwargs) o.lcaCount = kwargs.lca_count;
  if ('url' in kwargs) o.url = kwargs.url;
  return o;
}

const rows = pythonResults();
let failed = 0;

const W = 46;
console.log('='.repeat(96));
console.log('  GATE PARITY: web/gate-engine.js  vs  src/gate.py'.padEnd(96));
console.log('='.repeat(96));

for (const row of rows) {
  const js = evaluate(row.text, toJsOpts(row.kwargs));
  const jsRules = js.findings.map((f) => f.rule).sort();

  const diffs = [];
  if (js.verdict !== row.verdict) diffs.push(`verdict js=${js.verdict} py=${row.verdict}`);
  if (js.tier !== row.tier) diffs.push(`tier js=${js.tier} py=${row.tier}`);
  if (jsRules.join(',') !== row.rules.join(',')) {
    diffs.push(`rules js=[${jsRules}] py=[${row.rules}]`);
  }

  if (diffs.length) {
    failed += 1;
    console.log(`  FAIL  ${row.label.padEnd(W)}`);
    for (const d of diffs) console.log(`        ${d}`);
  } else {
    console.log(`  ok    ${row.label.padEnd(W)} ${row.verdict.padEnd(10)} ${row.tier}`);
  }
}

console.log('-'.repeat(96));
const total = rows.length;
console.log(`  ${total - failed}/${total} cases identical in both implementations`);
if (failed) {
  console.log('  The browser demo does NOT agree with the engine. Fix before shipping.');
}
console.log('='.repeat(96));

process.exit(failed ? 1 : 0);
