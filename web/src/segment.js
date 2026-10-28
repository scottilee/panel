// Exact evaluation of a finding's affected_segment_filter, with the same
// semantics as the backend recount (panel/synthesize.py's _clause_matches):
// clauses AND together, age_band is derived from age, numeric fields are
// coerced from strings, and a value no persona has simply matches nobody.
// Clicking a finding's segment filters with this, so the grid shows
// exactly the people the finding's segment_size counted.

const NUMERIC_FIELDS = new Set([
  'tech_comfort',
  'skepticism',
  'openness',
  'conscientiousness',
  'extraversion',
  'agreeableness',
  'neuroticism',
])

// Matches panel/personas.py's age_band_for.
export function ageBandFor(age) {
  if (age <= 24) return '18-24'
  if (age <= 34) return '25-34'
  if (age <= 49) return '35-49'
  if (age <= 64) return '50-64'
  return '65+'
}

function fieldValue(persona, field) {
  return field === 'age_band' ? ageBandFor(persona.age) : persona[field]
}

// NaN for anything that isn't a number, which then fails every comparison
// -- a clause the backend couldn't recount matches nobody here.
function coerce(field, target) {
  if (!NUMERIC_FIELDS.has(field)) return target
  return Array.isArray(target) ? target.map(Number) : Number(target)
}

export function clauseMatches(persona, { field, op, value }) {
  const v = fieldValue(persona, field)
  const t = coerce(field, value)
  switch (op) {
    case '==':
      return v === t
    case '!=':
      return v !== t
    case '<=':
      return v <= t
    case '>=':
      return v >= t
    case '<':
      return v < t
    case '>':
      return v > t
    case 'in':
      return Array.isArray(t) && t.includes(v)
    default:
      return false
  }
}

export function matchesSegment(persona, clauses) {
  return clauses.every((c) => clauseMatches(persona, c))
}
