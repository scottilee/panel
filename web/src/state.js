// useReducer reducer + selectors (stats, filters, deltas). Stage 1, Agent B.
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
}

export const initialState = {
  personaCount: 100,
  poolSize: 100,
  inputMode: 'text',
  artifactText: '',
  artifactUrl: '',
  label: '',
  status: STATUS.IDLE,
  personas: [],
  reactionsById: {},
  progress: { done: 0, total: 0 },
  synthesis: null,
  stats: null,
  startedAt: null,
  elapsedMs: 0,
  error: null,
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
      }

    case 'SET_INPUT_MODE':
      return { ...state, inputMode: action.mode }
    case 'SET_ARTIFACT_TEXT':
      return { ...state, artifactText: action.text }
    case 'SET_ARTIFACT_URL':
      return { ...state, artifactUrl: action.url }
    case 'SET_LABEL':
      return { ...state, label: action.label }

    case 'RUN_START':
      return {
        ...state,
        status: STATUS.PENDING,
        reactionsById: {},
        progress: { done: 0, total: state.personas.length },
        synthesis: null,
        stats: null,
        error: null,
        startedAt: Date.now(),
        elapsedMs: 0,
      }

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

    case 'TICK':
      return state.startedAt ? { ...state, elapsedMs: Date.now() - state.startedAt } : state

    default:
      return state
  }
}

// --- Selectors ---------------------------------------------------------

// filteredIds: null means no filter active (everyone counted, same
// behavior as before Stage 2); a Set restricts both `total` and the
// reactions averaged in to that segment (section 6.5: "recomputes the
// stats strip for the segment only").
export function selectLiveStats(state, filteredIds = null) {
  const allReactions = Object.values(state.reactionsById)
  const reactions = filteredIds ? allReactions.filter((r) => filteredIds.has(r.persona_id)) : allReactions
  const done = reactions.length
  const total = filteredIds ? filteredIds.size : state.progress.total || state.personas.length
  if (done === 0) return { done, total, meanSentiment: 0, wouldActPct: 0 }
  const meanSentiment = reactions.reduce((sum, r) => sum + r.sentiment, 0) / done
  const wouldActPct = (100 * reactions.filter((r) => r.would_act).length) / done
  return { done, total, meanSentiment, wouldActPct }
}

// --- Segment filters, section 6.5 ---------------------------------------

export function ageBandFor(age) {
  if (age <= 24) return '18-24'
  if (age <= 34) return '25-34'
  if (age <= 49) return '35-49'
  if (age <= 64) return '50-64'
  return '65+'
}

// tech_comfort and skepticism are 1-100 (section 3 said 1-5; widened by
// request on 2026-10-28). Tri-section matches panel/synthesize.py's
// _tech_band/_skepticism_band exactly -- the spec's literal "low 1-2, mid
// 3, high 4-5" text is stale, not a second source of truth.
export function scoreBand(value) {
  if (value <= 33) return 'low'
  if (value <= 66) return 'mid'
  return 'high'
}

export function personaMatchesFilter(persona, filter) {
  switch (filter.group) {
    case 'region':
      return persona.region === filter.value
    case 'ageBand':
      return ageBandFor(persona.age) === filter.value
    case 'incomeBand':
      return persona.income_band === filter.value
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
