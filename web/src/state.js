// useReducer reducer + selectors (stats, filters, deltas), plus the two
// enumerated Persona fields the filters depend on the order of.
//
// state.personas is the fixed, ordered sample for the current run (section
// 3: "same count always selects the same personas"). It's kept in sync with
// personaCount by SET_PERSONA_COUNT itself (via api.js's previewPersonas),
// not derived at render time, so the grid can re-lay out to a new N before
// Run is ever clicked (section 6.10).

export const STATUS = {
  IDLE: 'idle',
  PENDING: 'pending',
  STREAMING: 'streaming',
  SYNTHESIZING: 'synthesizing',
  DONE: 'done',
  ERROR: 'error',
  // Frontend-only terminal state for a user-initiated stop. The backend
  // reports "cancelled" too (an addition to section 3's Run.status,
  // flagged in schemas.py); keeping it distinct from ERROR is what lets
  // the UI avoid telling someone a failure happened when they pressed
  // the stop button themselves.
  CANCELLED: 'cancelled',
}

// Section 6.6/6.7: the second run of a compare. `visible` (the second
// textarea is open) is the N-lock condition per section 6.10 ("once a
// compare is in flight") -- deliberately broader than "actively
// streaming", since the lock should hold for the whole compare lifecycle
// once it starts, not just while reactions are landing.
const initialCompareState = {
  visible: false,
  text: '',
  status: STATUS.IDLE,
  runId: null,
  reactionsById: {},
  progress: { done: 0, total: 0 },
  stats: null,
  result: null,
  startedAt: null,
  elapsedMs: 0,
  error: null,
}

export const initialState = {
  // Both are placeholders until App.jsx's mount effect replaces poolSize
  // with the real pool length (GET /personas, now 1,000) and clamps the
  // count to it. 100 rather than the pool size on purpose: it's the
  // default panel size, and the first paint lays the grid out for it.
  personaCount: 100,
  poolSize: 100,
  inputMode: 'text',
  artifactText: '',
  artifactUrl: '',
  label: '',
  // True while `label` is a name borrowed from a chosen example rather
  // than something the user typed -- see SET_ARTIFACT_TEXT.
  labelFromExample: false,
  status: STATUS.IDLE,
  runId: null,
  personas: [],
  reactionsById: {},
  progress: { done: 0, total: 0 },
  synthesis: null,
  stats: null,
  startedAt: null,
  elapsedMs: 0,
  error: null,
  retrying: false,
  compare: initialCompareState,
}

export function reducer(state, action) {
  switch (action.type) {
    case 'SET_POOL_SIZE':
      return { ...state, poolSize: action.poolSize }

    case 'SET_PERSONA_COUNT':
      // Changing N clears any prior run state (section 6.10: "clears
      // compare state and requires a new run") and re-lays out the grid
      // immediately via the new personas list.
      return {
        ...state,
        personaCount: action.n,
        personas: action.personas,
        status: STATUS.IDLE,
        reactionsById: {},
        progress: { done: 0, total: action.personas.length },
        synthesis: null,
        stats: null,
        error: null,
        startedAt: null,
        elapsedMs: 0,
        compare: initialCompareState,
      }

    // The real client learns the run_id only once POST /run resolves.
    // Mock mode never dispatches this -- there is no real id to know, and
    // nothing downstream needs one in mock mode.
    case 'SET_RUN_ID':
      return { ...state, runId: action.runId }
    case 'COMPARE_SET_RUN_ID':
      return { ...state, compare: { ...state.compare, runId: action.runId } }

    case 'SET_INPUT_MODE':
      return { ...state, inputMode: action.mode }

    // Picking an example fills in both the text and its name. The name is
    // marked as borrowed so the next case can drop it again.
    case 'SELECT_EXAMPLE':
      return {
        ...state,
        artifactText: action.text,
        label: action.label,
        labelFromExample: true,
      }

    case 'SET_ARTIFACT_TEXT':
      // A borrowed label does not survive an edit to the text it came
      // with. Previously, picking "Parking policy notice v1" and then
      // pasting your own copy over it left the run -- and the verdict
      // header above it -- still labelled as the parking notice.
      return {
        ...state,
        artifactText: action.text,
        label: state.labelFromExample ? '' : state.label,
        labelFromExample: false,
      }

    case 'SET_ARTIFACT_URL':
      return { ...state, artifactUrl: action.url }

    case 'SET_LABEL':
      // Typed by hand, so it is the user's and nothing clears it.
      return { ...state, label: action.label, labelFromExample: false }

    case 'RUN_START':
      return {
        ...state,
        status: STATUS.PENDING,
        reactionsById: {},
        progress: { done: 0, total: state.personas.length },
        synthesis: null,
        stats: null,
        error: null,
        retrying: false,
        startedAt: Date.now(),
        elapsedMs: 0,
        compare: initialCompareState,
      }

    // Section 6.8: "never show a raw error to a judge" -- while retrying
    // is true, the UI shows "Retrying..." instead of surfacing the error
    // that triggered it.
    case 'SET_RETRYING':
      return { ...state, retrying: action.retrying }

    case 'REACTION':
      return {
        ...state,
        status: state.status === STATUS.PENDING ? STATUS.STREAMING : state.status,
        // Spreading preserves the reference for every untouched persona_id,
        // which is what lets Tile's React.memo bail out for the other 99+
        // tiles on each incoming reaction.
        reactionsById: { ...state.reactionsById, [action.reaction.persona_id]: action.reaction },
      }

    case 'PROGRESS':
      return { ...state, progress: action.progress }
    case 'SYNTHESIZING':
      return { ...state, status: STATUS.SYNTHESIZING }
    case 'SYNTHESIS':
      return { ...state, synthesis: action.synthesis }
    case 'DONE':
      return { ...state, status: STATUS.DONE, stats: action.stats }
    case 'ERROR':
      return { ...state, status: STATUS.ERROR, error: action.message }

    // Keeps whatever reactions already landed: they were paid for and
    // they are real, so the grid stays as it is rather than blanking.
    case 'CANCELLED':
      return { ...state, status: STATUS.CANCELLED, retrying: false, error: null }

    case 'TICK': {
      if (!state.startedAt && !state.compare.startedAt) return state
      const now = Date.now()
      return {
        ...state,
        elapsedMs: state.startedAt ? now - state.startedAt : state.elapsedMs,
        compare: state.compare.startedAt
          ? { ...state.compare, elapsedMs: now - state.compare.startedAt }
          : state.compare,
      }
    }

    // --- Section 6.6/6.7: compare (run B) -------------------------------

    case 'COMPARE_SET_TEXT':
      return { ...state, compare: { ...state.compare, visible: true, text: action.text } }

    case 'COMPARE_RUN_START':
      return {
        ...state,
        compare: {
          ...state.compare,
          visible: true,
          status: STATUS.PENDING,
          reactionsById: {},
          progress: { done: 0, total: state.personas.length },
          stats: null,
          result: null,
          error: null,
          startedAt: Date.now(),
          elapsedMs: 0,
        },
      }

    case 'COMPARE_REACTION':
      return {
        ...state,
        compare: {
          ...state.compare,
          status: state.compare.status === STATUS.PENDING ? STATUS.STREAMING : state.compare.status,
          reactionsById: {
            ...state.compare.reactionsById,
            [action.reaction.persona_id]: action.reaction,
          },
        },
      }

    case 'COMPARE_PROGRESS':
      return { ...state, compare: { ...state.compare, progress: action.progress } }

    case 'COMPARE_DONE':
      return { ...state, compare: { ...state.compare, status: STATUS.DONE, stats: action.stats } }

    case 'COMPARE_RESULT':
      return { ...state, compare: { ...state.compare, result: action.result } }

    case 'COMPARE_ERROR':
      return { ...state, compare: { ...state.compare, status: STATUS.ERROR, error: action.message } }

    default:
      return state
  }
}

// --- Selectors ---------------------------------------------------------

// filteredIds: null means no filter active (everyone counted, same
// behavior as when no filters existed); a Set restricts both `total` and the
// reactions averaged in to that segment (section 6.5: "recomputes the
// stats strip for the segment only").
// The arithmetic both selectors share. Mirrors panel/api/main.py's
// _compute_stats -- Run A and Run B had line-for-line copies of this.
function statsOver(reactions, total) {
  const done = reactions.length
  if (done === 0) return { done, total, meanSentiment: 0, wouldActPct: 0 }
  return {
    done,
    total,
    meanSentiment: reactions.reduce((sum, r) => sum + r.sentiment, 0) / done,
    wouldActPct: (100 * reactions.filter((r) => r.would_act).length) / done,
  }
}

export function selectLiveStats(state, filteredIds = null) {
  const allReactions = Object.values(state.reactionsById)
  const reactions = filteredIds ? allReactions.filter((r) => filteredIds.has(r.persona_id)) : allReactions
  const total = filteredIds ? filteredIds.size : state.progress.total || state.personas.length
  return statsOver(reactions, total)
}

// --- Segment filters, section 6.5 ---------------------------------------

// tech_comfort and skepticism are 1-100 (section 3 said 1-5; widened by
// request on 2026-10-28). Tri-section matches panel/synthesize.py's
// _tech_band/_skepticism_band exactly -- the spec's literal "low 1-2, mid
// 3, high 4-5" text is stale, not a second source of truth.
export function scoreBand(value) {
  if (value <= 33) return 'low'
  if (value <= 66) return 'mid'
  return 'high'
}

// The two enumerated Persona fields, in the same order as
// panel/schemas.py's REGIONS / INCOME_BANDS literals. Owned here because
// the filter logic below is what depends on their order; mock.js
// re-exports them.
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

// region/techComfort/skepticism stay chip-style ({group, value}, exact
// match). ageBand/incomeBand changed to range-style ({group, min, max} /
// {group, minIndex, maxIndex}) on 2026-10-29 -- age filters on the real
// persona.age number now, not the derived band; income filters on the
// band's ordinal position in INCOME_BANDS, since income has no raw
// number. Each range group has at most one entry in activeFilters (the
// current slider position), so this still composes correctly with
// selectFilteredPersonaIds's OR-within-group/AND-across-groups grouping.
export function personaMatchesFilter(persona, filter) {
  switch (filter.group) {
    case 'region':
      return persona.region === filter.value
    case 'ageBand':
      return persona.age >= filter.min && persona.age <= filter.max
    case 'incomeBand': {
      const idx = INCOME_BANDS.indexOf(persona.income_band)
      return idx >= filter.minIndex && idx <= filter.maxIndex
    }
    case 'techComfort':
      return scoreBand(persona.tech_comfort) === filter.value
    case 'skepticism':
      return scoreBand(persona.skepticism) === filter.value
    default:
      return true
  }
}

// Same-group chips OR together (picking two regions widens the segment to
// either); different groups AND (picking a region and a tech_comfort band
// narrows to both) -- the spec's "multiple chips combine with AND" reads
// most sensibly this way, since AND-ing two values from the same
// mutually-exclusive field (e.g. two regions) would always be empty.
// Returns null when no filters are active, meaning "everyone" -- callers
// treat that distinctly from an empty-but-filtered Set.
export function selectFilteredPersonaIds(personas, activeFilters) {
  if (activeFilters.length === 0) return null
  const byGroup = {}
  for (const f of activeFilters) {
    (byGroup[f.group] ??= []).push(f)
  }
  const groups = Object.values(byGroup)
  const ids = new Set()
  for (const persona of personas) {
    if (groups.every((groupFilters) => groupFilters.some((f) => personaMatchesFilter(persona, f)))) {
      ids.add(persona.id)
    }
  }
  return ids
}

export function selectElapsedSeconds(state) {
  return state.elapsedMs / 1000
}

export function selectIsRunning(state) {
  return state.status === STATUS.PENDING || state.status === STATUS.STREAMING || state.status === STATUS.SYNTHESIZING
}

// --- Compare (run B), section 6.6/6.7 -----------------------------------

export function selectCompareLiveStats(state) {
  return statsOver(
    Object.values(state.compare.reactionsById),
    state.compare.progress.total || state.personas.length,
  )
}

export function selectCompareElapsedSeconds(state) {
  return state.compare.elapsedMs / 1000
}

export function selectCompareIsRunning(state) {
  return state.compare.status === STATUS.PENDING || state.compare.status === STATUS.STREAMING
}

// Section 6.10: "the second textarea inherits and locks the persona-count
// control once a compare is in flight" -- for the whole lifecycle once it
// opens, not just while streaming.
export function selectCountLocked(state) {
  return state.compare.visible
}

// Per-persona delta classification for the grid's delta view (section
// 6.7). Sourced directly from both runs' live reactions rather than only
// CompareResult.deltas, since the schema's `flipped_to_act` only encodes
// the positive direction -- this derives both directions the same way,
// from the same ground truth.
export function selectDeltaByPersonaId(state) {
  const byId = {}
  for (const persona of state.personas) {
    const a = state.reactionsById[persona.id]
    const b = state.compare.reactionsById[persona.id]
    if (!a || !b) continue
    if (!a.would_act && b.would_act) byId[persona.id] = 'positive'
    else if (a.would_act && !b.would_act) byId[persona.id] = 'negative'
    else byId[persona.id] = 'unchanged'
  }
  return byId
}

// Section 6.6: clicking a finding's affected_segment chip applies that
// filter (reusing the same activeFilters shape). affected_segment_filter's
// fields don't map 1:1 onto filter chip groups -- age_band has no
// Persona field (it's derived from age), and the Big Five traits have no
// chip group at all -- clauses on those are
// skipped rather than erroring, since "apply what can be applied" is
// more useful than refusing the whole chip for one unmappable clause.
const SEGMENT_FIELD_TO_GROUP = {
  region: 'region',
  age_band: 'ageBand',
  income_band: 'incomeBand',
  tech_comfort: 'techComfort',
  skepticism: 'skepticism',
}

// age_band clauses only ever carry band names (via '==' or 'in'), never a
// raw numeric threshold against age itself -- there's no <=/>= case to
// handle for this field. Matches the AGE_MAX bound Filters.jsx's slider
// uses for "65+" (85), so a clause built from the UI and one built here
// agree on what "65+" means.
const AGE_BAND_RANGES = {
  '18-24': [18, 24],
  '25-34': [25, 34],
  '35-49': [35, 49],
  '50-64': [50, 64],
  '65+': [65, 85],
}

// Chips are positive selections with no negation, so a '!=' clause has to
// be turned into the complement set before it can be expressed as chips.
// Until this existed, '!=' fell through to the same handling as '==' and
// the chip selected *exactly the personas the finding excluded* -- the
// clause's own inverse. SEGMENT_OPS in panel/schemas.py permits '!=' and
// nothing in the synthesis prompt discourages it, so this is reachable.
//
// Complements are only expressible for the two unordered groups (region,
// and the tri-sected score bands). The age and income chips are a single
// contiguous range, so "every band except a middle one" cannot be
// represented at all -- those clauses are skipped, consistently with how
// unmappable fields are already handled.
function complementNames(all, excluded) {
  const drop = new Set(Array.isArray(excluded) ? excluded : [excluded])
  return all.filter((v) => !drop.has(v))
}

function ageRangeForClause(op, value) {
  if (op === '!=') return null
  const names = op === 'in' ? value : [value]
  const ranges = names.map((n) => AGE_BAND_RANGES[n]).filter(Boolean)
  if (!ranges.length) return null
  return { min: Math.min(...ranges.map((r) => r[0])), max: Math.max(...ranges.map((r) => r[1])) }
}

function incomeRangeForClause(op, value) {
  const names = op === 'in' ? value : [value]
  let idxs = names.map((n) => INCOME_BANDS.indexOf(n)).filter((i) => i >= 0)
  if (op === '!=') {
    // Only when the excluded band sits at one end, leaving the rest
    // contiguous.
    if (idxs.length !== 1) return null
    const excluded = idxs[0]
    if (excluded === 0) idxs = [1, INCOME_BANDS.length - 1]
    else if (excluded === INCOME_BANDS.length - 1) idxs = [0, INCOME_BANDS.length - 2]
    else return null
  }
  if (!idxs.length) return null
  return { minIndex: Math.min(...idxs), maxIndex: Math.max(...idxs) }
}

const SCORE_BANDS = ['low', 'mid', 'high']

function scoreBandsForClause(op, value) {
  if (op === '==') return [scoreBand(value)]
  if (op === '!=') return complementNames(SCORE_BANDS, scoreBand(value))
  if (op === 'in') return [...new Set(value.map(scoreBand))]
  if (op === '<=' || op === '<') {
    if (value <= 33) return ['low']
    if (value <= 66) return ['low', 'mid']
    return SCORE_BANDS
  }
  if (op === '>=' || op === '>') {
    if (value >= 67) return ['high']
    if (value >= 34) return ['mid', 'high']
    return SCORE_BANDS
  }
  return []
}

export function segmentFilterToChips(clauses) {
  const chips = []
  for (const clause of clauses) {
    const group = SEGMENT_FIELD_TO_GROUP[clause.field]
    if (!group) continue // Big Five or any other unmapped field -- skip
    if (group === 'techComfort' || group === 'skepticism') {
      for (const band of scoreBandsForClause(clause.op, clause.value)) {
        chips.push({ group, value: band })
      }
    } else if (group === 'ageBand') {
      const range = ageRangeForClause(clause.op, clause.value)
      if (range) chips.push({ group, ...range })
    } else if (group === 'incomeBand') {
      const range = incomeRangeForClause(clause.op, clause.value)
      if (range) chips.push({ group, ...range })
    } else if (clause.op === 'in') {
      for (const v of clause.value) chips.push({ group, value: v })
    } else if (clause.op === '!=') {
      for (const v of complementNames(REGIONS, clause.value)) chips.push({ group, value: v })
    } else {
      chips.push({ group, value: clause.value })
    }
  }
  return chips
}

// Grid column count and tile size (px) for N tiles. Anchored to the two
// data points the spec gives (N=100 -> 10 cols / 44px, N=1000 -> 32 cols /
// 12px) and interpolated on a log scale beyond N=100; below N=100, tiles
// grow toward a comfortable max instead of shrinking further.
export function gridLayoutFor(n) {
  const cols = Math.max(1, Math.ceil(Math.sqrt(n)))
  const N0 = 100
  const SIZE0 = 44
  const N1 = 1000
  const SIZE1 = 12
  const MAX_SIZE = 64
  const MIN_N = 10

  let tile
  if (n <= N0) {
    const t = Math.min(1, Math.max(0, (N0 - n) / (N0 - MIN_N)))
    tile = SIZE0 + t * (MAX_SIZE - SIZE0)
  } else {
    const t = Math.min(1, (Math.log(n) - Math.log(N0)) / (Math.log(N1) - Math.log(N0)))
    tile = SIZE0 + t * (SIZE1 - SIZE0)
  }
  return { cols, tile: Math.round(tile) }
}
