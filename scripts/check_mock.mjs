// Mock mode must never assert something about the user's copy that the
// copy does not contain. Three separate reports came from it doing so.
//   node scripts/check_mock.mjs
//
// Mock mode is synthetic by definition, but three separate bug reports
// came from it asserting things about the user's own copy that were
// false: identical reaction distributions for all three examples, a
// flat 19-in-100 flip rate against a measured 0-7, and findings about
// "$29" for text with no price in it. This pins all of that.
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'

const here = dirname(fileURLToPath(import.meta.url))
const M = await import(join(here, '../web/src/mock.js'))

const OWN = [
  'Our new parking app finds you a space in seconds. First month free, cancel any time, no card needed at signup.',
  'CITY LIBRARY: Opening hours change on Monday. We close at six on weekdays and stay shut on Sundays from now on.',
  'Join the running club. We meet Tuesdays at seven by the bridge and nobody is too slow to come along with us.',
]
const SPECIFICS = ['$29', 'free trial', 'cancellation policy', 'subscription', 'PlateUp', 'Nimbus']

let bad = 0
const ps = M.buildMockPersonas(30)

for (const text of OWN) {
  const rs = M.buildMockReactions(ps, text)
  const syn = M.buildMockSynthesis(ps, text)
  const blob = JSON.stringify(rs) + JSON.stringify(syn)
  const leaked = SPECIFICS.filter((t) => blob.includes(t) && !text.includes(t))
  if (leaked.length) {
    bad++
    console.log('WRONG leaked', JSON.stringify(leaked), 'for:', text.slice(0, 44))
  } else {
    console.log('OK    no invented specifics for:', text.slice(0, 44))
  }
  // Apply-and-compare must have a real phrase to replace.
  const ct = syn.highest_leverage_edit.current_text
  if (!ct || !text.includes(ct)) {
    bad++
    console.log('WRONG edit current_text is not in the artifact:', JSON.stringify(ct))
  }
}

// The examples keep their tailored copy -- that is the good demo.
const pricing = M.EXAMPLES.find((e) => e.id === 'pricing_v1').text
const synP = M.buildMockSynthesis(ps, pricing)
const okSpecific = JSON.stringify(synP).includes('$29') && pricing.includes('$29')
console.log((okSpecific ? 'OK   ' : 'WRONG') + ' pricing example still gets its tailored findings')
if (!okSpecific) bad++

// And the per-example distributions must still track the real measured
// numbers (README's anti-sycophancy table), or mock mode is back to
// painting a rosier picture than the product gives.
const REAL = {
  pricing_v1: { act: 2.7, mean: -0.28 },
  ad_v1: { act: 2.0, mean: -0.48 },
  policy_v1: { act: 0.0, mean: -0.3 },
}
const big = M.buildMockPersonas(1000)
for (const ex of M.EXAMPLES) {
  const rs = M.buildMockReactions(big, ex.text)
  const act = (100 * rs.filter((r) => r.would_act).length) / rs.length
  const mean = rs.reduce((s, r) => s + r.sentiment, 0) / rs.length
  const want = REAL[ex.id]
  const ok = Math.abs(act - want.act) <= 1.0 && Math.abs(mean - want.mean) <= 0.1
  if (!ok) bad++
  console.log(
    (ok ? 'OK   ' : 'WRONG') + ' ' + ex.id.padEnd(12),
    `act ${act.toFixed(1)}% (real ${want.act}%)`.padEnd(24),
    `mean ${mean.toFixed(2)} (real ${want.mean})`)
}

console.log(bad ? `\n${bad} PROBLEM(S)` : '\nmock mode asserts nothing it cannot support')
process.exit(bad ? 1 : 0)
