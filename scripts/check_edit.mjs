// Cases for web/src/edit.js, the edit applier behind "Apply and compare".
//   node scripts/check_edit.mjs
//
// A miss here doesn't error -- it would rerun the unedited copy and report
// ~0 flips as if the edit had failed -- so the null-on-miss contract
// matters as much as the replacements.
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const { applyEdit } = await import(join(here, '../web/src/edit.js'))

const CASES = [
  // [name, artifact, current_text, proposed_text, expected]
  ['exact match', 'price is $29 today', '$29', '$29/mo', 'price is $29/mo today'],
  ['first occurrence only', '$29 and $29', '$29', 'FREE', 'FREE and $29'],
  ['whitespace differs', 'price is\n  $29 today', 'price is $29', 'price is $39', 'price is $39 today'],
  ['regex characters', 'Save (up to) 50%+ now.', 'Save (up to) 50%+', 'Save 50%', 'Save 50% now.'],
  ['dollar sign in replacement', 'Only $5', 'Only $5', 'Only $& more', 'Only $& more'],
  ['absent phrase', 'nothing here', 'missing phrase', 'x', null],
  ['empty current_text', 'anything', '', 'x', null],
  ['whitespace-only current_text', 'anything', '   ', 'x', null],
]

let bad = 0
for (const [name, text, current, proposed, want] of CASES) {
  const got = applyEdit(text, current, proposed)
  const ok = got === want
  if (!ok) bad++
  console.log((ok ? 'OK   ' : 'WRONG'), name.padEnd(28), ok ? '' : `got ${JSON.stringify(got)}`)
}
console.log(bad ? `\n${bad} WRONG` : '\nall correct')
process.exit(bad ? 1 : 0)
