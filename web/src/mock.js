// Synthetic data shaped exactly like panel/schemas.py (build spec section 3),
// so the UI can be built and reviewed end to end before any backend exists
// (section 6.9). The timed SSE-shaped event emission on top of this data is
// stage 1's job (the mock SSE client); this file only produces valid shapes.

export const REGIONS = [
  'Northeast US',
  'Southeast US',
  'Midwest US',
  'Southwest US',
  'West US',
  'Western Europe',
  'Asia-Pacific',
  'Latin America',
]

export const INCOME_BANDS = ['<25k', '25-40k', '40-75k', '75-150k', '150k+']

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

function ageFor(i) {
  const bands = [21, 29, 41, 58, 70]
  return bands[i % bands.length] + (i % 7)
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
// each get a distinct, non-block-clustered full name instead of the first
// 20 cycling in lockstep with the last 20 (which produced e.g. "Dolores
// Park" five times over -- every Nth name repeating in lockstep -- before
// before this fix).
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
      // 1-100, not 1-5 (section 3 said 1-5; widened by request on 2026-10-28
      // for more variability). Different multipliers per trait so they're
      // not all just the same i value in disguise.
      tech_comfort: ((i * 7) % 100) + 1,
      skepticism: ((i * 13 + 5) % 100) + 1,
      // Big Five / OCEAN, 1-100 -- not in the original section 3 schema,
      // added by request alongside the skepticism/tech_comfort rescale.
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

const CONFUSIONS = [
  'Whether the $29 is monthly or one-time',
  'What happens after the free trial',
  'Who this is actually for',
  null,
]

const OBJECTIONS = [
  'No visible cancellation policy',
  'No price until the third screen',
  'Too much jargon to trust it',
  null,
]

const CHANGES = [
  "Say 'cancel anytime, one click' next to the price",
  'Put the price in the first sentence',
  'Drop the jargon, use plain words',
  'Show one real customer, not a stock photo',
]

const NON_NULL_CONFUSIONS = CONFUSIONS.filter((c) => c !== null)
const NON_NULL_OBJECTIONS = OBJECTIONS.filter((o) => o !== null)

export function buildMockReaction(persona, i) {
  const sentiment = ((i % 5) - 2)
  const atTwo = sentiment === 2
  return {
    persona_id: persona.id,
    sentiment,
    would_act: sentiment >= 1 && i % 3 !== 0,
    gut_reaction: pick(GUT_REACTIONS, i),
    top_confusion: atTwo && i % 2 === 0 ? null : pick(NON_NULL_CONFUSIONS, i),
    top_objection: atTwo && i % 2 === 1 ? null : pick(NON_NULL_OBJECTIONS, i),
    what_would_change_my_mind: pick(CHANGES, i),
    run_id: 'mock-run',
    latency_ms: 300 + (i % 7) * 50,
    raw_error: null,
  }
}

export function buildMockReactions(personas) {
  return personas.map((p, i) => buildMockReaction(p, i))
}

export function buildMockSynthesis(personas) {
  const evidenceFrom = (start) => personas.slice(start, start + 4).map((p) => p.id)
  return {
    headline: 'Pricing reads as a subscription to 41% of personas over 55',
    findings: [
      {
        claim: 'The $29 figure is read as monthly by most low-tech-comfort personas',
        affected_pct: 41,
        affected_segment: 'tech_comfort <= 33',
        affected_segment_filter: [{ field: 'tech_comfort', op: '<=', value: 33 }],
        evidence: evidenceFrom(0),
        severity: 'high',
      },
      {
        claim: 'High-skepticism personas object to the missing cancellation policy',
        affected_pct: 33,
        affected_segment: 'skepticism >= 70',
        affected_segment_filter: [{ field: 'skepticism', op: '>=', value: 70 }],
        evidence: evidenceFrom(10),
        severity: 'medium',
      },
      {
        claim: 'Southwest and Southeast US personas are the most price-anchored',
        affected_pct: 22,
        affected_segment: 'region in [Southwest US, Southeast US]',
        affected_segment_filter: [
          { field: 'region', op: 'in', value: ['Southwest US', 'Southeast US'] },
        ],
        evidence: evidenceFrom(20),
        severity: 'low',
      },
    ],
    highest_leverage_edit: {
      current_text: '$29',
      proposed_text: '$29 one-time. No subscription.',
      rationale: 'Resolves the top confusion for a third of personas without changing price.',
      predicted_flips: 22,
    },
    who_you_are_losing: 'Older, price-sensitive, low-trust readers who leave before the second paragraph.',
    who_you_are_winning: 'Tech-comfortable 25-40 year olds who skim and see a low number.',
  }
}

// flipped_to_act (and flipped_positive/negative below) are about the
// would_act decision, not sentiment sign -- section 4.4's prompt wording
// ("N personas moved to would_act... N moved away") is the one place the
// spec pins this unambiguously. sentiment_before/after still move too, so
// the grid's color wave has something to animate, but they're not what
// "flipped" means here.
// Bug found 2026-10-29: Run A and a mock "Run B" (apply-and-compare) used
// to call buildMockReactions(personas) identically -- that function is a
// pure function of the persona list only, never of the artifact text, so
// toggling Run A / Run B / Delta showed literally identical data (every
// tile "unchanged", identical stats) no matter what was actually "run".
// Fixing it means Run B has to be a deliberate, deterministic edit of
// Run A's actual reactions, and the compare result has to be computed
// from that same edit rather than invented independently of it.
function flipGroupsFor(personas) {
  return {
    positiveIds: new Set(personas.slice(0, 19).map((p) => p.id)),
    negativeIds: new Set(personas.slice(19, 22).map((p) => p.id)),
  }
}

// Run B = Run A's reactions with a fixed subset deliberately flipped --
// everyone else stays byte-identical (a real edit shouldn't move people
// it wasn't about).
export function buildMockReactionsB(personas, reactionsAById) {
  const { positiveIds, negativeIds } = flipGroupsFor(personas)
  return personas.map((p) => {
    const before = reactionsAById[p.id]
    if (!before) return before
    if (positiveIds.has(p.id)) {
      return { ...before, would_act: true, sentiment: Math.min(2, before.sentiment + 2) }
    }
    if (negativeIds.has(p.id)) {
      return { ...before, would_act: false, sentiment: Math.max(-2, before.sentiment - 1) }
    }
    return before
  })
}

// Computed from the ACTUAL reactionsA/reactionsB a run produced (whatever
// N was chosen), not a fixed N=100 constant -- so it stays correct at any
// persona count instead of silently mismatching smaller/larger runs.
export function buildMockCompareResult(personas, reactionsAById, reactionsBById) {
  const { positiveIds, negativeIds } = flipGroupsFor(personas)
  const targeted = personas.filter((p) => positiveIds.has(p.id) || negativeIds.has(p.id))
  // A targeted persona whose would_act was already at its target value
  // (e.g. a "flip to true" persona who happened to already be true in
  // Run A) is a non-event, not a flip in either direction -- counting it
  // as "flipped_negative" just because it isn't "flipped_positive" (the
  // first version of this fix did exactly that) inflates the negative
  // count with people who didn't actually change. Check both directions
  // explicitly instead of inferring one from "not the other".
  let flippedPositive = 0
  let flippedNegative = 0
  const deltas = targeted.map((p) => {
    const a = reactionsAById[p.id]
    const b = reactionsBById[p.id]
    const toAct = !a.would_act && b.would_act
    const awayFromAct = a.would_act && !b.would_act
    if (toAct) flippedPositive++
    if (awayFromAct) flippedNegative++
    return {
      persona_id: p.id,
      sentiment_before: a.sentiment,
      sentiment_after: b.sentiment,
      flipped_to_act: toAct,
    }
  })
  return {
    run_a: 'mock-run',
    run_b: 'mock-run-b',
    deltas,
    flipped_positive: flippedPositive,
    flipped_negative: flippedNegative,
    would_act_delta_pct: flippedPositive - flippedNegative,
    summary: 'Naming the price as one-time flips 19 skeptical, low-tech-comfort readers toward acting.',
  }
}

// Section 6.8 example buttons, mock-mode source. Real text from
// data/examples/*.txt, embedded here (this is browser code, it can't read
// the filesystem) so the three buttons load real-looking content without
// any backend. MOCK_SYNTHESIS.highest_leverage_edit.current_text ('$29')
// is guaranteed to appear verbatim in the pricing example below -- that's
// the example the "Apply this edit and compare" flow is built against.
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

export const MOCK_PERSONAS = buildMockPersonas(100)
export const MOCK_REACTIONS = buildMockReactions(MOCK_PERSONAS)
export const MOCK_SYNTHESIS = buildMockSynthesis(MOCK_PERSONAS)
// No static MOCK_COMPARE_RESULT: it has to be computed from whatever
// Run A/Run B reactions a given run actually produced (see api.js's
// getCompareResult), not fixed at N=100 ahead of time.
