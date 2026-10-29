// Mock mode must never assert something about the user's copy that the
// copy does not contain, and its numbers must mean what the UI says.
//   node scripts/check_mock.mjs
//
// Pins: no invented specifics (e.g. "$29") for arbitrary text, an edit
// that targets a real phrase, per-example distributions that track the
// real measurements, findings recounted the way the backend does it, and
// stats that leave failed reactions out.
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
const byId = (rs) => Object.fromEntries(rs.map((r) => [r.persona_id, r]))

for (const text of OWN) {
  const rs = M.buildMockReactions(ps, text)
  const syn = M.buildMockSynthesis(ps, text)
  const cmp = M.buildMockCompareResult(ps, byId(rs), byId(M.buildMockReactionsB(ps, byId(rs))))
  const blob = JSON.stringify(rs) + JSON.stringify(syn) + cmp.summary
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

// The three examples must not borrow each other's vocabulary. The check
// above only ever looked at arbitrary pasted text, so it missed the real
// case: "specific" once meant "is a recognized example", and the parking
// notice was described in the pricing page's words -- headline, findings
// and a suggested edit quoting a $29 it does not contain.
const EXAMPLE_WORDS = {
  pricing_v1: ['$29', 'PlateUp', 'meal'],
  ad_v1: ['Nimbus', '4.8', 'FDIC', 'APY'],
  policy_v1: ['permit', 'Riverton', 'parking'],
}
for (const e of M.EXAMPLES) {
  const rs = M.buildMockReactions(ps, e.text)
  const syn = M.buildMockSynthesis(ps, e.text, rs)
  const blob = JSON.stringify(rs) + JSON.stringify(syn)
  const foreign = Object.entries(EXAMPLE_WORDS)
    .filter(([id]) => id !== e.id)
    .flatMap(([, words]) => words)
    .filter((w) => blob.includes(w) && !e.text.includes(w))
  if (foreign.length) {
    bad++
    console.log('WRONG', e.id, 'borrows another example\'s words:', JSON.stringify(foreign))
  } else {
    console.log('OK    no borrowed specifics for:', e.id)
  }
  // The suggested edit must quote the artifact, or apply-and-compare has
  // nothing to replace.
  const ct = syn.highest_leverage_edit.current_text
  if (!ct || !e.text.includes(ct)) {
    bad++
    console.log('WRONG', e.id, 'edit current_text is not in the artifact:', JSON.stringify(ct))
  } else {
    console.log('OK    edit quotes the artifact:', e.id)
  }
}

// A reader shown as strongly negative must not say something cheerful:
// gut_reaction used to be picked by a hash unrelated to the sentiment.
for (const e of M.EXAMPLES) {
  const rs = M.buildMockReactions(M.buildMockPersonas(300), e.text)
  const shared = new Map()
  for (const r of rs) {
    if (!shared.has(r.gut_reaction)) shared.set(r.gut_reaction, new Set())
    shared.get(r.gut_reaction).add(r.sentiment)
  }
  const straddles = [...shared].filter(([, bands]) => bands.size > 1)
  if (straddles.length) {
    bad++
    console.log('WRONG', e.id, 'one line spans several sentiment bands:', straddles[0][0])
  } else {
    console.log('OK    every gut reaction matches its sentiment:', e.id)
  }
}

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

// Findings carry the backend's recount semantics: affected_pct is the
// negative share *within* the segment, segment_size is the segment's head
// count, severity is counted, and the headline quotes the top row's number.
{
  const people = M.buildMockPersonas(200)
  const rs = M.buildMockReactions(people, pricing)
  const syn = M.buildMockSynthesis(people, pricing, rs)
  const reactions = byId(rs)
  let ok = true
  for (const f of syn.findings) {
    const [c] = f.affected_segment_filter
    const members = people.filter((p) =>
      c.op === '<=' ? p[c.field] <= c.value : c.op === '>=' ? p[c.field] >= c.value : c.value.includes(p[c.field]),
    )
    const neg = members.filter((p) => reactions[p.id].sentiment < 0).length
    const want = Math.round((100 * neg) / members.length)
    if (f.segment_size !== members.length || f.affected_pct !== want) ok = false
    if (Math.abs(f.segment_pct - (100 * members.length) / people.length) > 1e-9) ok = false
  }
  if (!syn.headline.includes(`${syn.findings[0].affected_pct}%`)) ok = false
  // Severity is counted the backend's way and the rows sort by it.
  const rank = { high: 0, medium: 1, low: 2 }
  for (const f of syn.findings) {
    if (f.severity !== M.countedSeverity(f.affected_pct, syn.overall_negative_pct)) ok = false
  }
  for (let i = 1; i < syn.findings.length; i++) {
    if (rank[syn.findings[i - 1].severity] > rank[syn.findings[i].severity]) ok = false
  }
  const overall = Math.round((100 * rs.filter((r) => r.sentiment < 0).length) / rs.length)
  if (syn.overall_negative_pct !== overall) ok = false
  if (!ok) bad++
  console.log((ok ? 'OK   ' : 'WRONG') + ' findings are recounted from the mock reactions')
}

// Stats leave failed reactions out of the averages, like the backend.
{
  const rs = [
    { persona_id: 'p001', sentiment: 2, would_act: true, latency_ms: 1, raw_error: null },
    { persona_id: 'p002', sentiment: 0, would_act: false, latency_ms: 1, raw_error: 'boom' },
  ]
  const st = M.buildMockStats(rs)
  const ok = st.mean_sentiment === 2 && st.would_act_pct === 100 && st.failed_reactions === 1
  if (!ok) bad++
  console.log((ok ? 'OK   ' : 'WRONG') + ' stats exclude failed reactions')
}

// Clicking a finding's segment filters to exactly the people its
// segment_size counted -- not an approximation by band chips.
{
  const S = await import(join(here, '../web/src/state.js'))
  const people = M.buildMockPersonas(300)
  const syn = M.buildMockSynthesis(people, pricing, M.buildMockReactions(people, pricing))
  let ok = true
  for (const f of syn.findings) {
    const ids = S.selectFilteredPersonaIds(people, [S.segmentFilter(f)])
    if (ids.size !== f.segment_size) ok = false
  }
  if (!ok) bad++
  console.log((ok ? 'OK   ' : 'WRONG') + ' a finding\'s segment filter matches its segment_size')
}

// The clause semantics match panel/synthesize.py's recount, including the
// cases band chips got wrong.
{
  const G = await import(join(here, '../web/src/segment.js'))
  const p = { age: 52, region: 'West US', income_band: '40-75k', tech_comfort: 70, skepticism: 40 }
  const cases = [
    [[{ field: 'tech_comfort', op: '>', value: 66 }], true],
    [[{ field: 'tech_comfort', op: '>=', value: '71' }], false], // string coerced
    [[{ field: 'skepticism', op: '>=', value: 34 }, { field: 'skepticism', op: '<=', value: 50 }], true],
    [[{ field: 'skepticism', op: '>=', value: 34 }, { field: 'skepticism', op: '<=', value: 39 }], false],
    [[{ field: 'age_band', op: 'in', value: ['50-64', '65+'] }], true],
    [[{ field: 'age_band', op: '==', value: '50-59' }], false], // invented band matches nobody
    [[{ field: 'region', op: '!=', value: 'West US' }], false],
    [[{ field: 'tech_comfort', op: '==', value: 'low' }], false], // non-numeric matches nobody
  ]
  const ok = cases.every(([clauses, want]) => G.matchesSegment(p, clauses) === want)
  if (!ok) bad++
  console.log((ok ? 'OK   ' : 'WRONG') + ' segment clauses evaluate like the backend recount')
}

// Compare numbers share one denominator: personas usable in both runs.
{
  const people = M.buildMockPersonas(100)
  const a = byId(M.buildMockReactions(people, pricing))
  a[people[0].id] = { ...a[people[0].id], raw_error: 'boom' }
  const b = byId(M.buildMockReactionsB(people, a))
  const r = M.buildMockCompareResult(people, a, b)
  const shared = people.filter((p) => !a[p.id].raw_error && !b[p.id].raw_error)
  const actA = shared.filter((p) => a[p.id].would_act).length
  const actB = shared.filter((p) => b[p.id].would_act).length
  const ok =
    r.compared === shared.length &&
    r.deltas.length === shared.length &&
    Math.abs(r.would_act_delta_pct - (100 * (actB - actA)) / shared.length) < 1e-9 &&
    r.flipped_positive - r.flipped_negative === actB - actA
  if (!ok) bad++
  console.log((ok ? 'OK   ' : 'WRONG') + ' compare flips and would-act change share one denominator')
}

// Fast mode imitates the measured direction: onto the fence (fewer
// negative), at least as willing to act, and no null objections.
{
  const people = M.buildMockPersonas(200)
  const text = M.EXAMPLES[1].text
  const slow = M.buildMockReactions(people, text)
  const fast = M.buildMockReactions(people, text, { fast: true })
  const negative = (rs) => rs.filter((r) => r.sentiment < 0).length
  const acts = (rs) => rs.filter((r) => r.would_act).length
  const ok =
    negative(fast) < negative(slow) &&
    acts(fast) >= acts(slow) &&
    fast.every((r) => r.top_confusion !== null && r.top_objection !== null)
  if (!ok) bad++
  console.log((ok ? 'OK   ' : 'WRONG') + ' fast mode moves people onto the fence and never leaves objections empty')
}

// The headline names the top row's segment, for every example and at
// sizes where a different segment can come out on top.
{
  const words = { 'low tech comfort': 'low-tech-comfort', 'high skepticism': 'high-skepticism', Southwest: 'Southwest' }
  let ok = true
  for (const n of [10, 50, 100, 300]) {
    const people = M.buildMockPersonas(n)
    for (const ex of [...M.EXAMPLES, { text: OWN[0] }]) {
      const rs = M.buildMockReactions(people, ex.text)
      const syn = M.buildMockSynthesis(people, ex.text, rs)
      const top = syn.findings[0]
      const key = Object.keys(words).find((k) => top.affected_segment.startsWith(k))
      if (!syn.headline.includes(words[key]) || !syn.headline.includes(`${top.affected_pct}%`)) ok = false
    }
  }
  if (!ok) bad++
  console.log((ok ? 'OK   ' : 'WRONG') + ' the headline is about the top finding')
}

console.log(bad ? `\n${bad} PROBLEM(S)` : '\nmock mode asserts nothing it cannot support')
// --- Market comparison honesty ---------------------------------------------
//
// The comparison names which pages share a theme, and the user can open
// every one of those pages and read it. So the claims have to survive
// being read. Two things used to break that: the competitor pages were
// savings-account copy regardless of the artifact (so a parking notice was
// compared against APY offers), and the shared themes were artifact-
// specific (so "whether my street is in a new zone" was asserted of pages
// that are not about parking at all).
const normalize = (t) =>
  t.toLowerCase().replace(/[^a-z0-9 ]/g, '').split(/\s+/)
    .filter((w) => !['a','an','the','is','my','i','this','it','of','for','to','in','on','and','what','who','whether','does','not','be','are','am','being'].includes(w))
    .sort().join(' ')

for (const e of M.EXAMPLES) {
  const cmp = M.buildMockComparison(e.text, 50)
  const shared = [...cmp.shared_confusions, ...cmp.shared_objections]
  const yours = [...cmp.only_yours_confusions, ...cmp.only_yours_objections]
  const labels = new Set(cmp.pages.map((p) => p.label))

  // 1. Every page a theme cites must exist.
  const ghosts = [...shared, ...yours].flatMap((t) => t.pages.filter((l) => !labels.has(l)))
  if (ghosts.length) {
    bad++
    console.log('WRONG', e.id, 'theme cites pages that do not exist:', JSON.stringify(ghosts))
  } else {
    console.log('OK    every cited page exists:', e.id)
  }

  // 2. A theme cannot be both category-wide and yours alone.
  const sharedKeys = new Set(shared.map((t) => normalize(t.theme)))
  const both = yours.filter((t) => sharedKeys.has(normalize(t.theme))).map((t) => t.theme)
  if (both.length) {
    bad++
    console.log('WRONG', e.id, 'theme is in both columns at once:', JSON.stringify(both))
  } else {
    console.log('OK    columns do not contradict each other:', e.id)
  }

  // 3. Competitor pages must not be another example's subject matter.
  const foreign = Object.entries(EXAMPLE_WORDS)
    .filter(([id]) => id !== e.id)
    .flatMap(([, words]) => words)
    .filter((w) => cmp.pages.slice(1).some((p) => p.text.includes(w)))
  if (foreign.length) {
    bad++
    console.log('WRONG', e.id, 'competitor pages are about another example:', JSON.stringify(foreign))
  } else {
    console.log('OK    competitor pages match the category:', e.id)
  }
}

// 4. The category column must be identical for every artifact, because it
// is the one claim made about pages whose text the user can open. Drawn
// from the generic pool, it is true of any bland marketing page; drawn
// from the artifact's own pool it was not, and nothing mechanical catches
// that -- "whether my money is actually FDIC insured" asserted of a page
// that says "FDIC insured to $250,000" needs a reader to notice. Pinning
// the invariant is what stops it coming back.
const sharedPerExample = M.EXAMPLES.map((e) => {
  const cmp = M.buildMockComparison(e.text, 50)
  return [...cmp.shared_confusions, ...cmp.shared_objections].map((t) => t.theme).join(' | ')
})
if (new Set(sharedPerExample).size !== 1) {
  bad++
  console.log('WRONG the category column differs per artifact, so it is making')
  console.log('      artifact-specific claims about pages it did not read:')
  M.EXAMPLES.forEach((e, i) => console.log('        ' + e.id + ': ' + sharedPerExample[i]))
} else {
  console.log('OK    the category column is artifact-independent')
}

process.exit(bad ? 1 : 0)
