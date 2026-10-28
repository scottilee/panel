// Synthetic data shaped exactly like panel/schemas.py, so the UI can be
// built and reviewed end to end with no backend and no credits spent. The
// timed SSE-shaped event emission on top of this data is api.js's
// startMockRun; this file only produces valid shapes.

import { matchesSegment } from './segment.js'
import { REGIONS, INCOME_BANDS } from './state.js'

const FIRST_NAMES = [
  'Dolores', 'Marcus', 'Priya', 'Jin', 'Fatima', 'Logan', 'Ingrid', 'Tomas',
  'Nadia', 'Victor', 'Chloe', 'Kwame', 'Elena', 'Ravi', 'Sofia', 'Dmitri',
  'Amara', 'Lucas', 'Yuki', 'Beatriz',
]

const LAST_NAMES = [
  'Reyes', 'Holloway', 'Nair', 'Park', 'Haddad', 'Ferreira', 'Johansen',
  'Varga', 'Okafor', 'Novak', 'Lindqvist', 'Mensah', 'Rossi', 'Patel',
  'Bianchi', 'Volkov', 'Adeyemi', 'Moreau', 'Tanaka', 'Silva',
]

const OCCUPATIONS = [
  'retired school secretary', 'freelance electrician', 'pediatric nurse',
  'warehouse supervisor', 'high school teacher', 'small business owner',
  'software support technician', 'long-haul truck driver', 'barista and student',
  'municipal clerk', 'graphic designer', 'insurance adjuster', 'line cook',
  'bookkeeper', 'rideshare driver', 'dental hygienist', 'farm manager',
  'IT help desk lead', 'social worker', 'retail store manager',
]

const VALUE_POOL = [
  'frugality', 'family', 'distrust of fine print', 'privacy', 'self-reliance',
  'brand loyalty once earned', 'punctuality', 'community reputation',
  'environmental impact', 'getting a straight answer',
]

const TRIGGER_POOL = [
  'recommendation from a friend', 'clear cancellation policy', 'a visible phone number',
  'reviews with specifics, not stars', 'a free trial with no card required',
  'seeing the total price upfront', 'a deadline that feels real',
]

const PEEVE_POOL = [
  'auto-renewal', 'jargon', 'having to call to cancel', 'hidden fees',
  'fake urgency banners', 'forms that ask for a phone number too early',
  'stock photos of smiling strangers',
]

function pick(pool, seed) {
  return pool[seed % pool.length]
}

function pickN(pool, seed, n) {
  const out = []
  for (let i = 0; i < n; i++) out.push(pool[(seed + i * 7) % pool.length])
  return [...new Set(out)]
}

// Deterministic 32-bit mixing hash. Anything derived from a plain `i % k`
// where k divides the grid's 10 columns lands in fixed columns and shows
// up as vertical stripes (the real persona pool had exactly this problem
// with its 5-cycle age/income bands). Hashing first breaks that alignment
// while staying fully reproducible.
function hash32(n) {
  let h = Math.imul(n ^ 0x9e3779b9, 2654435761)
  h ^= h >>> 15
  h = Math.imul(h, 2246822507)
  h ^= h >>> 13
  return h >>> 0
}

function ageFor(i) {
  const bands = [21, 29, 41, 58, 70]
  return bands[hash32(i * 3 + 1) % bands.length] + (hash32(i) % 7)
}

const VOICE_TEMPLATES = [
  (first) => `I read everything twice and I still assume there's a catch, ${first} thinking out loud.`,
  () => "If I can't tell what it costs in ten seconds I'm out.",
  () => "I've been burned by a subscription before, so I check for the cancel link first.",
  () => "I skim for the number and the catch, in that order.",
  () => "I don't trust anything that won't show me the price before I give an email.",
  (first) => `${first} here -- I close the tab the second a form asks for my phone number before I've seen a price.`,
  () => "I screenshot the terms before I click anything, in case they change them later.",
  () => "If the testimonials don't have last names, I assume they're made up.",
  () => "I check the footer for a real company address before I trust anything above it.",
  () => "Flashy countdown timers make me trust it less, not more.",
  (first) => `Honestly, ${first} just wants the number, not the story around it.`,
  () => "I read the cancellation policy before the pitch, every time.",
  () => "If it needs a call to sign up, I assume it's hard to cancel too.",
  () => "I look for one real customer with a real name before I believe any of it.",
  () => "Three exclamation points in the headline and I'm already skeptical.",
]

// Deterministic Fisher-Yates (mulberry32), not Math.random, matching this
// file's existing determinism convention.
function seededShuffle(array, seed) {
  let s = seed
  const next = () => {
    s |= 0
    s = (s + 0x6d2b79f5) | 0
    let t = Math.imul(s ^ (s >>> 15), 1 | s)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
  const out = array.slice()
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(next() * (i + 1))
    ;[out[i], out[j]] = [out[j], out[i]]
  }
  return out
}

// All FIRST_NAMES x LAST_NAMES pairs (400), shuffled once so personas 0..99
// each get a distinct, non-block-clustered full name instead of first and
// last names cycling in lockstep.
const NAME_PAIRS = seededShuffle(
  FIRST_NAMES.flatMap((first) => LAST_NAMES.map((last) => [first, last])),
  1337,
)

function nameFor(i) {
  // Beyond 400 (the full pool), names necessarily repeat -- acceptable for
  // stress-testing the grid at N=1000, where individual identity matters
  // less than at N=100.
  return NAME_PAIRS[i % NAME_PAIRS.length]
}

export function buildMockPersonas(n = 100) {
  const personas = []
  for (let i = 0; i < n; i++) {
    const [first, last] = nameFor(i)
    const name = `${first} ${last}`
    const region = pick(REGIONS, i)
    personas.push({
      id: `p${String(i + 1).padStart(3, '0')}`,
      name,
      age: ageFor(i),
      gender: i % 2 === 0 ? 'female' : 'male',
      location: `${region} city ${i % 12}`,
      region,
      income_band: pick(INCOME_BANDS, i + 1),
      occupation: pick(OCCUPATIONS, i),
      household: i % 3 === 0 ? 'lives alone' : 'lives with spouse, kids nearby',
      // All traits 1-100. Different multipliers per trait so they're not
      // all just the same i value in disguise.
      tech_comfort: ((i * 7) % 100) + 1,
      skepticism: ((i * 13 + 5) % 100) + 1,
      // Big Five / OCEAN.
      openness: ((i * 17 + 11) % 100) + 1,
      conscientiousness: ((i * 23 + 3) % 100) + 1,
      extraversion: ((i * 29 + 7) % 100) + 1,
      agreeableness: ((i * 31 + 2) % 100) + 1,
      neuroticism: ((i * 37 + 9) % 100) + 1,
      values: pickN(VALUE_POOL, i, 3),
      buying_triggers: pickN(TRIGGER_POOL, i + 2, 2),
      pet_peeves: pickN(PEEVE_POOL, i + 4, 3),
      voice: VOICE_TEMPLATES[i % VOICE_TEMPLATES.length](first),
    })
  }
  return personas
}

const GUT_REACTIONS = [
  "Is this a subscription? It doesn't say, which means yes.",
  "Fine, I'll keep reading.",
  "Too much jargon, I'm already skimming past it.",
  "Okay this is actually clear, surprising.",
  "Where's the catch, there's always a catch.",
]

// Two sets, and which one is used depends on whether the artifact is a
// recognized example.
//
// The specific set quotes the pricing example's actual content ("$29",
// "the free trial"). That reads well when the pricing example is what was
// submitted -- and is a fabrication about anything else. Mock data is
// synthetic by definition, but it must not assert things about the user's
// copy that are false (scripts/check_mock.mjs pins this).
const SPECIFIC_CONFUSIONS = [
  'Whether the $29 is monthly or one-time',
  'What happens after the free trial',
  'Who this is actually for',
  null,
]

const SPECIFIC_OBJECTIONS = [
  'No visible cancellation policy',
  'No price until the third screen',
  'Too much jargon to trust it',
  null,
]

const SPECIFIC_CHANGES = [
  "Say 'cancel anytime, one click' next to the price",
  'Put the price in the first sentence',
  'Drop the jargon, use plain words',
  'Show one real customer, not a stock photo',
]

// The generic set quotes nothing. Every line is true of almost any
// public-facing copy, so it stays honest about arbitrary input.
const GENERIC_CONFUSIONS = [
  'What exactly I am being asked to do',
  'Who this is meant for',
  'What it would cost me',
  null,
]

const GENERIC_OBJECTIONS = [
  'Nothing here backs up the claims',
  'It does not say what happens next',
  'Too vague to act on',
  null,
]

const GENERIC_CHANGES = [
  'Say plainly what the reader should do next',
  'Put one concrete number in the first line',
  'Name who this is for',
  'Back the main claim with something checkable',
]

const GENERIC_GUT_REACTIONS = [
  "I'm not sure what they want me to do here.",
  'Fine, I skimmed it.',
  "Too vague for me to care.",
  'Clear enough, I suppose.',
  "What's the catch?",
]

function contentFor(artifactText) {
  const specific = !!calibrationFor(artifactText).match
  return specific
    ? {
        guts: GUT_REACTIONS,
        confusions: SPECIFIC_CONFUSIONS.filter((c) => c !== null),
        objections: SPECIFIC_OBJECTIONS.filter((o) => o !== null),
        changes: SPECIFIC_CHANGES,
        nullableConfusions: SPECIFIC_CONFUSIONS,
        nullableObjections: SPECIFIC_OBJECTIONS,
      }
    : {
        guts: GENERIC_GUT_REACTIONS,
        confusions: GENERIC_CONFUSIONS.filter((c) => c !== null),
        objections: GENERIC_OBJECTIONS.filter((o) => o !== null),
        changes: GENERIC_CHANGES,
        nullableConfusions: GENERIC_CONFUSIONS,
        nullableObjections: GENERIC_OBJECTIONS,
      }
}

// Per-example calibration, taken from the real anti-sycophancy
// measurements on the shipped 1,000-persona pool (README's table) and
// from the first full N=1000 run of the policy notice. Reality differs
// sharply per artifact -- the parking notice offers no action to take, so
// a real run returns 0.0% would-act -- and mock mode must not paint a
// rosier picture than the product gives.
//
// `shift` biases the score (negative = harsher); `actRate` is the share
// of positive-sentiment personas who also flip would_act. Shifts were
// solved numerically against the real means; scripts/check_mock.mjs
// verifies both at N=1000.
//
// Matched on distinctive phrases, not single words, so a user's own copy
// that happens to mention parking isn't mistaken for the example. Each
// phrase is checked to appear in its example by scripts/check_mock.mjs.
const ARTIFACT_CALIBRATION = [
  // real: mean -0.28, 2.7% would act -> mock: -0.29, 2.6%
  { match: 'PlateUp', shift: -6, actRate: 0.1 },
  // real: mean -0.48, 2.0% would act -> the harshest of the three
  { match: 'Nimbus is a financial technology company', shift: -10, actRate: 0.08 },
  // real: mean -0.30, 0.0% would act -- a civic notice offers no action
  // to take, so nobody "converts" and mock mode must say so too
  { match: 'RESIDENTIAL PERMIT PARKING', shift: -6, actRate: 0 },
]
// Arbitrary pasted text: no measurement to match, so this sits in the
// same range as the examples rather than pretending to a figure.
// No `match` key, which is how contentFor tells "this is one of the three
// examples" from "this is something the user pasted".
const DEFAULT_CALIBRATION = { shift: -6, actRate: 0.12 }

export function calibrationFor(artifactText) {
  if (!artifactText) return DEFAULT_CALIBRATION
  return (
    ARTIFACT_CALIBRATION.find((c) => artifactText.includes(c.match)) ?? DEFAULT_CALIBRATION
  )
}

// Sentiment hashes the index (a plain `i % 5` cycle would line up with the
// grid's columns as stripes) and skews by skepticism, so filtering to
// high-skepticism looks more negative, the way real model output does.
export function buildMockReaction(persona, i, calibration = DEFAULT_CALIBRATION, content = null) {
  const c = content ?? contentFor(null)
  const noise = hash32(i * 7 + 13) % 100
  const score = (100 - persona.skepticism) * 0.6 + noise * 0.4 + calibration.shift
  const sentiment = score < 18 ? -2 : score < 38 ? -1 : score < 62 ? 0 : score < 82 ? 1 : 2
  const atTwo = sentiment === 2
  return {
    persona_id: persona.id,
    sentiment,
    would_act: sentiment >= 1 && (hash32(i) % 100) / 100 < calibration.actRate,
    gut_reaction: pick(c.guts, hash32(i + 1)),
    top_confusion: atTwo && hash32(i) % 2 === 0 ? null : pick(c.confusions, hash32(i + 2)),
    top_objection: atTwo && hash32(i) % 2 === 1 ? null : pick(c.objections, hash32(i + 3)),
    what_would_change_my_mind: pick(c.changes, hash32(i + 4)),
    run_id: 'mock-run',
    latency_ms: 300 + (hash32(i) % 7) * 50,
    raw_error: null,
  }
}

export function buildMockReactions(personas, artifactText) {
  const calibration = calibrationFor(artifactText)
  const content = contentFor(artifactText)
  return personas.map((p, i) => buildMockReaction(p, i, calibration, content))
}

// Longest sentence in the artifact, used as the edit's current_text for
// unrecognized input. It has to be a real substring, or Apply and compare
// has nothing to replace.
function longestSentence(artifactText) {
  const sentences = (artifactText ?? '')
    .split(/(?<=[.!?])\s+|\n+/)
    .map((x) => x.trim())
    .filter((x) => x.split(/\s+/).length >= 4 && x.length <= 160)
  if (!sentences.length) return null
  return sentences.reduce((a, b) => (b.length > a.length ? b : a))
}


// affected_pct = share of the segment's members (with a usable reaction)
// who reacted negatively; segment_size/segment_pct = how big the segment
// is. Computed the way the backend recounts real findings, so mock mode
// shows numbers that mean what the UI says they mean.
function recount(personas, reactionsById, clauses) {
  const members = personas.filter((p) => matchesSegment(p, clauses))
  const answered = members.map((p) => reactionsById[p.id]).filter((r) => r && !r.raw_error)
  const negative = answered.filter((r) => r.sentiment < 0).length
  return {
    affected_pct: answered.length ? Math.round((100 * negative) / answered.length) : 0,
    segment_size: members.length,
    segment_pct: personas.length ? (100 * members.length) / personas.length : 0,
  }
}

export function buildMockSynthesis(personas, artifactText, reactions = []) {
  // Evidence ids are drawn from the personas this run actually has, and
  // the offsets wrap, so every cited id has a tile in the grid at any N.
  // Finding.evidence requires 3 to 5 entries, so this needs >= 3 personas
  // -- the N slider's floor is 10.
  const evidenceFrom = (start) =>
    Array.from({ length: Math.min(4, personas.length) }, (_, i) => personas[(start + i) % personas.length].id)
  const reactionsById = Object.fromEntries(reactions.map((r) => [r.persona_id, r]))

  // The segments and percentages are real either way: they are computed
  // over the mock personas and reactions, and clicking a segment chip
  // filters the grid to exactly the people it names. What changes with
  // the artifact is the prose, which otherwise claims things about copy
  // that may not contain them.
  const specific = !!calibrationFor(artifactText).match
  const segments = [
    {
      affected_segment: 'low tech comfort (33 or below)',
      affected_segment_filter: [{ field: 'tech_comfort', op: '<=', value: 33 }],
      severity: 'high',
    },
    {
      affected_segment: 'high skepticism (70 or above)',
      affected_segment_filter: [{ field: 'skepticism', op: '>=', value: 70 }],
      severity: 'medium',
    },
    {
      affected_segment: 'Southwest and Southeast US',
      affected_segment_filter: [{ field: 'region', op: 'in', value: ['Southwest US', 'Southeast US'] }],
      severity: 'low',
    },
  ]
  const claims = specific
    ? [
        'The $29 figure is read as monthly by low-tech-comfort personas',
        'High-skepticism personas object to the missing cancellation policy',
        'Southwest and Southeast US personas are the most price-anchored',
      ]
    : [
        'Low-tech-comfort personas could not tell what they were being asked to do',
        'High-skepticism personas wanted something checkable before acting',
        'Southwest and Southeast US personas were the least moved',
      ]
  const findings = segments.map((seg, i) => ({
    claim: claims[i],
    ...recount(personas, reactionsById, seg.affected_segment_filter),
    ...seg,
    evidence: evidenceFrom(i * 10),
  }))

  const edit = specific
    ? {
        current_text: '$29',
        proposed_text: '$29 one-time. No subscription.',
        rationale: 'Resolves the top confusion for low-tech-comfort readers without changing the price.',
      }
    : (() => {
        const target = longestSentence(artifactText)
        return target
          ? {
              current_text: target,
              proposed_text: `${target.replace(/[.!?]+$/, '')} — and here is the one number that proves it.`,
              rationale: 'Gives the sentence carrying the most weight something concrete to stand on.',
            }
          : {
              current_text: '',
              proposed_text: '',
              rationale: 'No sentence long enough to rewrite.',
            }
      })()

  const lead = findings[0].affected_pct
  return {
    headline: specific
      ? `The $29 reads as a subscription: ${lead}% of low-tech-comfort readers reacted negatively`
      : `${lead}% of low-tech-comfort readers reacted negatively and could not tell what this asks of them`,
    findings,
    highest_leverage_edit: { ...edit, predicted_flips: Math.max(1, Math.round(personas.length * 0.05)) },
    who_you_are_losing: specific
      ? 'Older, price-sensitive, low-trust readers who leave before the second paragraph.'
      : 'Older, low-trust readers who leave before the second paragraph.',
    who_you_are_winning: specific
      ? 'Tech-comfortable 25-40 year olds who skim and see a low number.'
      : 'Tech-comfortable 25-40 year olds who skim and give it the benefit of the doubt.',
  }
}

// RunStats for a mock run, with the backend's rules: failed reactions are
// left out of the averages, and the call counts follow the real pipeline
// (one Nano call per persona, one Super condensing call per 10 reactions
// above 150 personas, one Ultra synthesis).
const MOCK_SHARD_THRESHOLD = 150
const MOCK_SHARD_SIZE = 10

export function buildMockStats(reactions) {
  const usable = reactions.filter((r) => !r.raw_error)
  const n = usable.length
  return {
    mean_sentiment: n ? usable.reduce((sum, r) => sum + r.sentiment, 0) / n : 0,
    would_act_pct: n ? (100 * usable.filter((r) => r.would_act).length) / n : 0,
    failed_reactions: reactions.length - n,
    nano_calls: reactions.length,
    super_calls: reactions.length > MOCK_SHARD_THRESHOLD ? Math.ceil(reactions.length / MOCK_SHARD_SIZE) : 0,
    ultra_calls: 1,
    nano_ms_total: reactions.reduce((sum, r) => sum + (r.latency_ms ?? 0), 0),
    ultra_ms: 900,
  }
}

// Mock Run B is a deliberate, deterministic edit of Run A's actual
// reactions, and the compare result is computed from that same edit, so
// the Run A / Run B / Delta views genuinely differ.
//
// "Flipped" means the would_act decision changed, not sentiment sign, the
// same definition panel/compare.py uses. sentiment_before/after move too,
// so the grid's colour wave has something to animate.
//
// Sized to the real measured effect, not to look impressive: across six
// edit variants against real runs, one good edit flipped 0-7 people in
// 100. Scaled by panel size so the rate holds at any N.
const MOCK_FLIP_RATE = 0.05
const MOCK_BACKFIRE_RATE = 0.01

function flipGroupsFor(personas) {
  const positive = Math.max(1, Math.round(personas.length * MOCK_FLIP_RATE))
  const negative = Math.max(1, Math.round(personas.length * MOCK_BACKFIRE_RATE))
  return {
    positiveIds: new Set(personas.slice(0, positive).map((p) => p.id)),
    negativeIds: new Set(personas.slice(positive, positive + negative).map((p) => p.id)),
  }
}

// Run B = Run A's reactions with a fixed subset deliberately flipped --
// everyone else stays byte-identical (a real edit shouldn't move people
// it wasn't about).
export function buildMockReactionsB(personas, reactionsAById) {
  const { positiveIds, negativeIds } = flipGroupsFor(personas)
  // A persona with no Run A reaction is skipped rather than mapped to
  // undefined; callers read `.persona_id` off every element.
  return personas.flatMap((p) => {
    const before = reactionsAById[p.id]
    if (!before) return []
    if (positiveIds.has(p.id)) {
      return { ...before, would_act: true, sentiment: Math.min(2, before.sentiment + 2) }
    }
    if (negativeIds.has(p.id)) {
      return { ...before, would_act: false, sentiment: Math.max(-2, before.sentiment - 1) }
    }
    return before
  })
}

// Computed from the reactions both runs actually produced, with
// panel/compare.py's rules: only personas with a usable reaction in BOTH
// runs are compared, and the flips and the would-act change share that one
// denominator (`compared`).
export function buildMockCompareResult(personas, reactionsAById, reactionsBById) {
  const shared = personas.filter((p) => {
    const a = reactionsAById[p.id]
    const b = reactionsBById[p.id]
    return a && b && !a.raw_error && !b.raw_error
  })
  let flippedPositive = 0
  let flippedNegative = 0
  let actA = 0
  let actB = 0
  const deltas = shared.map((p) => {
    const a = reactionsAById[p.id]
    const b = reactionsBById[p.id]
    const toAct = !a.would_act && b.would_act
    if (toAct) flippedPositive++
    if (a.would_act && !b.would_act) flippedNegative++
    if (a.would_act) actA++
    if (b.would_act) actB++
    return {
      persona_id: p.id,
      sentiment_before: a.sentiment,
      sentiment_after: b.sentiment,
      flipped_to_act: toAct,
    }
  })
  const compared = shared.length
  return {
    run_a: 'mock-run',
    run_b: 'mock-run-b',
    deltas,
    compared,
    flipped_positive: flippedPositive,
    flipped_negative: flippedNegative,
    would_act_delta_pct: compared ? (100 * (actB - actA)) / compared : 0,
    // Built from the numbers just computed, since CompareBar shows the
    // sentence and the counters side by side. Generic wording: this
    // function doesn't know which artifact was edited.
    summary:
      `The edit moves ${flippedPositive} ${flippedPositive === 1 ? 'reader' : 'readers'} toward acting` +
      (flippedNegative ? ` and pushes ${flippedNegative} away` : '') +
      `.`,
  }
}

// Example buttons, mock-mode source. Real text from data/examples/*.txt,
// embedded here (this is browser code, it can't read the filesystem). The
// pricing example's tailored edit ('$29') appears verbatim in its text, so
// Apply and compare has something to replace.
export const EXAMPLES = [
  {
    id: 'pricing_v1',
    label: 'Pricing page v1',
    text: `PlateUp — Meal Planning Made Simple

Stop staring at the fridge wondering what to cook. PlateUp builds your
entire week of dinners around what you already have, what your family
actually likes, and how much time you've got on a given night.

How it works

Tell us your household size, any allergies, and the three or four meals
your family already loves. Every Sunday, PlateUp sends you seven dinners,
a shoppable grocery list sorted by aisle, and swap options if Tuesday
turns into a takeout night. Recipes top out at 30 minutes, and everything
scales automatically if you're cooking for 2 or for 6.

Why people switch

"I used to spend forty minutes a week just deciding what to make. Now I
spend four." — early user feedback from our beta

Thousands of households use PlateUp to cut food waste, stop ordering
delivery out of decision fatigue, and get dinner on the table without
the Sunday-night spiral. The grocery list alone saves most families
around $40 a week in impulse buys.

Pricing

$29

Get your first week of meal plans built around your family's taste in
under five minutes. No meal kits, no mystery boxes showing up at your
door — just a plan and a list, built for the kitchen you already have.

Start Planning

Questions? Our support team usually responds within a few hours. PlateUp
works with any grocery store, any diet, and any size kitchen.`,
  },
  {
    id: 'ad_v1',
    label: 'Fintech ad v1',
    text: `Stop letting your bank keep the upside.

Most checking accounts pay you basically nothing while your bank reinvests
your deposits and keeps the spread. Nimbus flips that. Our High-Yield
Vault runs on dynamic yield optimization, routing your idle cash across a
basket of FDIC-insured partner banks to capture the best available APY
every single day — automatically, with zero action from you.

Right now that APY is sitting at 4.8%, nearly 11x the national average on
a traditional savings account. No teaser rate that drops after 90 days.
No minimum balance tiers. No "relationship banking" hoops.

How it works:

Link your existing bank account, tell Nimbus how much of your paycheck to
sweep into the Vault, and let our allocation engine handle the rest. Our
real-time rate arbitrage means your money is always parked wherever the
yield curve favors you that day, across our network of partner
institutions, without you lifting a finger.

Because the Vault is structured as a cash management account rather than
a single deposit account, your balance benefits from extended FDIC
pass-through coverage well beyond the standard $250,000 limit at any one
bank.

Over 140,000 people have already moved their emergency fund to Nimbus.
Average time to set up: 6 minutes. Average extra yield captured in year
one: $612 per $10,000 deposited, compared to the national average savings
rate.

Your money should be working as hard as you do.

Open your Vault — nimbus.money/vault

Nimbus is a financial technology company, not a bank. Banking services
provided by Nimbus's partner banks, Members FDIC.`,
  },
  {
    id: 'policy_v1',
    label: 'Parking policy notice v1',
    text: `CITY OF RIVERTON — NOTICE OF RESIDENTIAL PERMIT PARKING PROGRAM EXPANSION

Effective the first of next month, the City of Riverton will expand its
Residential Permit Parking (RPP) Program to additional zones within the
Oak Grove, Millbrook, and Station District neighborhoods, as authorized
under Municipal Code Chapter 11, Section 4.

Background

The RPP Program was established to manage on-street parking demand in
areas experiencing spillover from nearby commercial corridors, transit
hubs, and event venues. Following a petition process initiated by
affected block associations and a traffic study conducted by the
Department of Transportation, the City Council voted on October 12th to
expand eligible zones.

What this means

Streets within the newly designated zones will transition to permit-only
parking during posted hours. Vehicles parked on these streets without a
valid permit or visitor pass during restricted hours will be subject to
citation. Restricted hours and signage will be posted at each affected
block no later than two weeks prior to enforcement.

Residents of record within a designated zone, as determined by the
Assessor's parcel boundaries on file with the City, are eligible to apply
for permits through the Department of Transportation's online portal or
in person at City Hall, Room 114.

The first permit per household is included as part of the standard
application. Each additional permit, and each visitor pass beyond the
two provided per household annually, is subject to the fee schedule
maintained by the Department of Transportation and published separately
on the City's website.

Residents who are unsure whether their specific block falls within a
newly designated zone should consult the zone map published on the
Department of Transportation's website or contact the Parking Services
Division directly.

Questions regarding this notice may be directed to the Department of
Transportation, Parking Services Division, at the number listed on the
City's official website.`,
  },
]
