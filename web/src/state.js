// useReducer reducer + selectors (stats, filters, deltas), plus the two
// enumerated Persona fields the filters depend on the order of.

import { matchesSegment } from './segment.js'
//
// state.personas is the fixed, ordered sample for the current run (the
// same count always selects the same personas). It's kept in sync with
// personaCount by SET_PERSONA_COUNT itself (via api.js's previewPersonas),
// not derived at render time, so the grid can re-lay out to a new N before
// Run is ever clicked.

export const STATUS = {
  IDLE: 'idle',
  PENDING: 'pending',
  STREAMING: 'streaming',
  SYNTHESIZING: 'synthesizing',
  DONE: 'done',
  ERROR: 'error',
  // Terminal state for a user-initiated stop, which the backend reports
  // as "cancelled" too. Kept distinct from ERROR so the UI never tells
  // someone a failure happened when they pressed the stop button.
  CANCELLED: 'cancelled',
}

// The second run of a compare. `visible` (the second textarea is open)
// is the N-lock condition -- deliberately broader than "actively
// streaming", since the lock holds for the whole compare lifecycle once
// it starts, not just while reactions are landing.
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
      // Changing N clears any prior run and compare state and re-lays out
      // the grid immediately via the new personas list.
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
      // with, or pasting your own copy over an example would leave the
      // run labelled as that example.
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

    // While retrying is true, the UI shows "Retrying..." instead of the
    // error that triggered the cached-example fallback.
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

    // --- Compare (run B) ------------------------------------------------

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

// The arithmetic Run A and Run B share, matching the backend's stats: a
// reaction whose model call failed (raw_error set) is counted as arrived
// but left out of the averages, since its sentiment 0 / would_act false
// are placeholders rather than an answer.
function statsOver(reactions, total) {
  const done = reactions.length
  const usable = reactions.filter((r) => !r.raw_error)
  const failed = done - usable.length
  if (usable.length === 0) return { done, total, usable: 0, failed, meanSentiment: 0, wouldActPct: 0 }
  return {
    done,
    total,
    usable: usable.length,
    failed,
    meanSentiment: usable.reduce((sum, r) => sum + r.sentiment, 0) / usable.length,
    wouldActPct: (100 * usable.filter((r) => r.would_act).length) / usable.length,
  }
}

// filteredIds: null means no filter active (everyone counted); a Set
// restricts both `total` and the reactions averaged to that segment.

export function selectLiveStats(state, filteredIds = null) {
  const allReactions = Object.values(state.reactionsById)
  const reactions = filteredIds ? allReactions.filter((r) => filteredIds.has(r.persona_id)) : allReactions
  const total = filteredIds ? filteredIds.size : state.progress.total || state.personas.length
  return statsOver(reactions, total)
}

// --- Segment filters ----------------------------------------------------

// tech_comfort and skepticism are 1-100. Tri-sected at the same cutoffs
// as the backend's segment bands (panel/synthesize.py) so a chip and a
// finding agree on what "low" means.
export function scoreBand(value) {
  if (value <= 33) return 'low'
  if (value <= 66) return 'mid'
  return 'high'
}

// The two enumerated Persona fields, in the same order as
// panel/schemas.py's REGIONS / INCOME_BANDS literals. Owned here because
// the filter logic below is what depends on their order.
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

// segment is a finding's affected_segment_filter, applied exactly
// ({group, clauses, label}; see segment.js). region/techComfort/skepticism
// are chip-style ({group, value}, exact match). ageBand/incomeBand are range-style ({group, min, max} /
// {group, minIndex, maxIndex}): age filters on the real persona.age
// number, not the derived band; income filters on the band's ordinal
// position in INCOME_BANDS, since income has no raw number. Each range group has at most one entry in activeFilters (the
// current slider position), so this still composes correctly with
// selectFilteredPersonaIds's OR-within-group/AND-across-groups grouping.
export function personaMatchesFilter(persona, filter) {
  switch (filter.group) {
    case 'segment':
      return matchesSegment(persona, filter.clauses)
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
// narrows to both). AND-ing two values from the same mutually-exclusive
// field (e.g. two regions) would always be empty.
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

// --- Compare (run B) ---------------------------------------------------

// Same filter semantics as selectLiveStats, so switching the view to Run B
// or Delta keeps describing the segment on screen.
export function selectCompareLiveStats(state, filteredIds = null) {
  const allReactions = Object.values(state.compare.reactionsById)
  const reactions = filteredIds ? allReactions.filter((r) => filteredIds.has(r.persona_id)) : allReactions
  const total = filteredIds ? filteredIds.size : state.compare.progress.total || state.personas.length
  return statsOver(reactions, total)
}

export function selectCompareElapsedSeconds(state) {
  return state.compare.elapsedMs / 1000
}

export function selectCompareIsRunning(state) {
  return state.compare.status === STATUS.PENDING || state.compare.status === STATUS.STREAMING
}

// The persona-count control locks once a compare opens, for its whole
// lifecycle, so both runs are always the same N.
export function selectCountLocked(state) {
  return state.compare.visible
}

// Per-persona delta classification for the grid's delta view. Sourced
// from both runs' live reactions rather than CompareResult.deltas, whose
// `flipped_to_act` only encodes the positive direction. A persona whose
// call failed in either run is left unclassified, matching the backend,
// which excludes those from the flip counts.
export function selectDeltaByPersonaId(state) {
  const byId = {}
  for (const persona of state.personas) {
    const a = state.reactionsById[persona.id]
    const b = state.compare.reactionsById[persona.id]
    if (!a || !b || a.raw_error || b.raw_error) continue
    if (!a.would_act && b.would_act) byId[persona.id] = 'positive'
    else if (a.would_act && !b.would_act) byId[persona.id] = 'negative'
    else byId[persona.id] = 'unchanged'
  }
  return byId
}

// Clicking a finding's segment applies its affected_segment_filter
// exactly, as one removable filter, rather than approximating it with the
// band chips: "skepticism >= 70" is not the same people as the "high"
// band (67+), and the grid has to show the people segment_size counted.
export function segmentFilter(finding) {
  return { group: 'segment', clauses: finding.affected_segment_filter, label: finding.affected_segment }
}

export const SCORE_BANDS = ['low', 'mid', 'high']

// Grid column count and tile size (px) for N tiles. Anchored at N=100 ->
// 10 cols / 44px and N=1000 -> 32 cols / 12px and interpolated on a log scale beyond N=100; below N=100, tiles
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
