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

function voiceFor(name, i) {
  const templates = [
    `I read everything twice and I still assume there's a catch, ${name.split(' ')[0]} thinking out loud.`,
    "If I can't tell what it costs in ten seconds I'm out.",
    "I've been burned by a subscription before, so I check for the cancel link first.",
    "I skim for the number and the catch, in that order.",
    "I don't trust anything that won't show me the price before I give an email.",
  ]
  return templates[i % templates.length]
}

export function buildMockPersonas(n = 100) {
  const personas = []
  for (let i = 0; i < n; i++) {
    const first = pick(FIRST_NAMES, i)
    const last = pick(LAST_NAMES, i + 3)
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
      voice: voiceFor(name, i),
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
function buildMockDelta(persona, reactionsBefore, wouldActBefore, wouldActAfter) {
  const before = reactionsBefore.find((r) => r.persona_id === persona.id)
  const sentimentBefore = before ? before.sentiment : -1
  const sentimentAfter = !wouldActBefore && wouldActAfter
    ? Math.min(2, sentimentBefore + 2)
    : wouldActBefore && !wouldActAfter
      ? Math.max(-2, sentimentBefore - 1)
      : sentimentBefore
  return {
    persona_id: persona.id,
    sentiment_before: sentimentBefore,
    sentiment_after: sentimentAfter,
    flipped_to_act: !wouldActBefore && wouldActAfter,
  }
}

export function buildMockCompareResult(personas, reactionsBefore) {
  const flippedPositive = personas.slice(0, 19)
  const flippedNegative = personas.slice(19, 22)
  const deltas = [
    ...flippedPositive.map((p) => buildMockDelta(p, reactionsBefore, false, true)),
    ...flippedNegative.map((p) => buildMockDelta(p, reactionsBefore, true, false)),
  ]
  return {
    run_a: 'mock-run',
    run_b: 'mock-run-b',
    deltas,
    flipped_positive: flippedPositive.length,
    flipped_negative: flippedNegative.length,
    would_act_delta_pct: flippedPositive.length - flippedNegative.length,
    summary: 'Naming the price as one-time flips 19 skeptical, low-tech-comfort readers toward acting.',
  }
}

export const MOCK_PERSONAS = buildMockPersonas(100)
export const MOCK_REACTIONS = buildMockReactions(MOCK_PERSONAS)
export const MOCK_SYNTHESIS = buildMockSynthesis(MOCK_PERSONAS)
export const MOCK_COMPARE_RESULT = buildMockCompareResult(MOCK_PERSONAS, MOCK_REACTIONS)
