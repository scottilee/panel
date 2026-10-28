// Real fetch + SSE client. Swapped for mock.js behind ?mock=1. Section 6.9.
//
// Both branches emit the same event shapes (section 3's SSE event list:
// reaction, progress, synthesizing, synthesis, done, error) so the rest of
// the app never has to know which one it's talking to. The real branch is
// POST /run plus an EventSource on /run/{id}/stream; the mock branch
// synthesizes the same sequence on timers, with no network and no credits
// spent.

import {
  buildMockCompareResult,
  buildMockPersonas,
  buildMockReactions,
  buildMockReactionsB,
  buildMockSynthesis,
  EXAMPLES,
} from './mock.js'

// Mock mode's pool is synthesized on demand (buildMockPersonas takes any
// n). This matched the real pool when it was 100 and still does now that
// it's 1,000 -- which is the point: mock mode has to exercise the same
// grid layout, filter and tile-size behaviour at the same N as a real run,
// with no API calls and no credits spent (section 6.2's N=1000 case).
export const MOCK_POOL_SIZE = 1000

const REACTION_INTERVAL_MS = 80
const SYNTHESIS_DELAY_MS = 500

export function isMockMode() {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).has('mock')
}

// The real pool, fetched once and cached -- previewPersonas needs it
// synchronously (section 6.10: the grid re-lays out instantly as the
// slider moves, before Run is ever clicked), and a fresh fetch per call
// would both be slow and risk a different array each time.
let realPoolCache = null

async function loadRealPool() {
  if (realPoolCache) return realPoolCache
  const res = await fetch('/personas')
  if (!res.ok) throw new Error(`GET /personas failed: HTTP ${res.status}`)
  realPoolCache = await res.json()
  return realPoolCache
}

export async function getPoolSize() {
  if (isMockMode()) return MOCK_POOL_SIZE
  const pool = await loadRealPool()
  return pool.length
}

// Synchronous: lets the grid re-lay out to a new N the instant the slider
// moves, before Run is ever clicked (section 6.10). Real mode: the first
// N of the cached pool, in the exact on-disk order panel.personas.
// sample_stable uses server-side for the actual run -- same rule on both
// sides, so the preview always shows the identities a real run will
// actually use (see sample_stable's docstring for why this isn't the
// same `sample()` random.Random(seed).sample the CLI/stage-6 path uses).
export function previewPersonas(n) {
  if (isMockMode()) return buildMockPersonas(n)
  if (!realPoolCache) {
    throw new Error('persona pool not loaded yet -- call getPoolSize() first')
  }
  return realPoolCache.slice(0, n)
}

// previousReactionsById: only passed for a compare's second run (Run B),
// and only meaningful in mock mode -- it's what makes mock Run B a
// deliberate, deterministic edit of Run A's actual data (see mock.js's
// buildMockReactionsB); without it, Run B used to be generated the exact
// same way as Run A (buildMockReactions depends only on the persona list,
// never on the artifact text), so toggling Run A/Run B/Delta showed no
// visible change at all. The real backend ignores it entirely -- it
// computes genuinely different reactions from genuinely different model
// calls on the edited text, no hint needed.
//
// text/label: only used by the real client (POST /run's body). Mock mode
// ignores them -- its reactions are a pure function of the persona list.
//
// Returns a handle synchronously ({cancel()}), even though starting a
// real run needs an async POST before the run_id (and therefore the
// EventSource) can even exist -- `cancelled` covers the gap so a cancel
// called before the POST resolves still takes effect.
// cached: section 6.8's demo fallback -- real mode only. Appends
// ?cached=true, which the backend only honors if `text` matches one of
// the real example files byte-for-byte (api/main.py's
// _find_cached_example); mock mode ignores it, since it has no such
// concept.
export function startRun({ text, url, label, personaCount, onEvent, previousReactionsById, cached }) {
  if (isMockMode()) return startMockRun({ personaCount, onEvent, previousReactionsById })
  return startRealRun({ text, url, label, personaCount, onEvent, cached })
}

function startRealRun({ text, url, label, personaCount, onEvent, cached = false }) {
  let cancelled = false
  let es = null

  ;(async () => {
    let runId
    try {
      const res = await fetch(`/run${cached ? '?cached=true' : ''}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // RunRequest is text XOR url, so send exactly one. JSON.stringify
        // drops undefined keys, which is what makes that work here.
        body: JSON.stringify({
          text: text || undefined,
          url: url || undefined,
          label: label || undefined,
          persona_count: personaCount,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        onEvent({ type: 'error', data: { message: body.detail || `POST /run failed: HTTP ${res.status}` } })
        return
      }
      ;({ run_id: runId } = await res.json())
    } catch (e) {
      onEvent({ type: 'error', data: { message: String(e) } })
      return
    }
    if (cancelled) return

    // In URL mode the artifact's text only exists server-side (the
    // backend fetched and extracted it), but the UI needs it locally:
    // "Apply and compare" rewrites one phrase of the artifact text and
    // reruns, which with an empty local copy would silently no-op. One
    // extra GET of the run record is cheaper than widening POST /run's
    // response shape (a section 3 contract).
    let fetchedText
    if (url) {
      try {
        const record = await fetch(`/run/${runId}`).then((r) => (r.ok ? r.json() : null))
        fetchedText = record?.artifact?.text
      } catch {
        // Non-fatal: the run itself is already underway. Apply-and-compare
        // stays disabled for this run rather than the run failing.
      }
    }

    // Not one of section 3's SSE events -- a local-only signal so the
    // caller (App.jsx) can stash the run_id for POST /compare later.
    // Mock mode never emits this; real callers must handle it.
    onEvent({ type: 'run_started', data: { run_id: runId, artifact_text: fetchedText } })

    es = new EventSource(`/run/${runId}/stream`)
    for (const type of ['reaction', 'progress', 'synthesizing', 'synthesis', 'done']) {
      es.addEventListener(type, (e) => {
        onEvent({ type, data: e.data ? JSON.parse(e.data) : undefined })
        if (type === 'done') es.close()
      })
    }
    es.addEventListener('error', (e) => {
      // Both the server's named "error" SSE event (section 3) and the
      // browser's own native connection-failure event arrive through this
      // same listener. A real server-sent event is a MessageEvent and
      // always has .data (even if empty); the browser's native failure
      // event is a plain Event and never has a .data property at all --
      // check that first, not readyState, which can still read OPEN for
      // a brief moment right as the server closes the stream.
      if (e.data !== undefined) {
        onEvent({ type: 'error', data: JSON.parse(e.data) })
        es.close()
      } else if (es.readyState === EventSource.CLOSED) {
        // Native failure, and the browser has given up retrying (as
        // opposed to a transient hiccup it's silently reconnecting from).
        onEvent({ type: 'error', data: { message: 'connection lost' } })
      }
    })
  })()

  return {
    cancel() {
      cancelled = true
      es?.close()
    },
  }
}

// Section 6.8: three example buttons, loaded from GET /examples in the
// real client. Mock mode sources the same shape from mock.js.
export async function getExamples() {
  if (isMockMode()) return EXAMPLES
  const res = await fetch('/examples')
  if (!res.ok) throw new Error(`GET /examples failed: HTTP ${res.status}`)
  return res.json()
}

// Section 6.6's "Apply this edit and compare": mock mode computes the
// result from the two reaction sets the run actually produced (so it's
// correct at any N, and actually reflects a real Run A vs Run B
// difference) rather than a fixed, disconnected canned object. Still
// fast and synchronous -- no network, no delay -- matching section 6.9's
// "no real computation cost" intent, just not "hardcoded regardless of
// input". The real branch below POSTs /compare instead.
// personas/reactionsAById: mock mode only (it recomputes Run B locally,
// never calling a server). runA/runB: real mode only (the two run_ids
// POST /compare needs; the backend already has both runs' real reactions
// in memory, no need to send them). Each branch ignores the args it
// doesn't need rather than the caller having to know which to supply.
export async function getCompareResult({ personas, reactionsAById, runA, runB }) {
  if (isMockMode()) {
    // buildMockReactionsB is deterministic (same personas + reactionsA
    // always produce the same Run B), so recomputing it here gives the
    // exact same reactions startMockRun already streamed into Run B's
    // live state -- no need to thread the accumulated state back in.
    const reactionsBById = {}
    for (const r of buildMockReactionsB(personas, reactionsAById)) reactionsBById[r.persona_id] = r
    return buildMockCompareResult(personas, reactionsAById, reactionsBById)
  }
  const res = await fetch('/compare', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_a: runA, run_b: runB }),
  })
  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `POST /compare failed: HTTP ${res.status}`)
  }
  return res.json()
}

// Deterministic Fisher-Yates permutation (no Math.random) so reactions
// arrive out of persona order on every run, the same way the real
// asyncio.as_completed fan-out does (section 5.2) -- this is what exercises
// "tiles are placed by persona_id, not arrival order."
function completionOrder(n) {
  const order = Array.from({ length: n }, (_, i) => i)
  for (let i = n - 1; i > 0; i--) {
    const j = (i * 2654435761) % (i + 1)
    ;[order[i], order[j]] = [order[j], order[i]]
  }
  return order
}

function average(nums) {
  return nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : 0
}

function percent(items, pred) {
  return items.length ? (100 * items.filter(pred).length) / items.length : 0
}

function startMockRun({ personaCount, onEvent, previousReactionsById }) {
  const personas = buildMockPersonas(personaCount)
  const reactions = previousReactionsById
    ? buildMockReactionsB(personas, previousReactionsById)
    : buildMockReactions(personas)
  const order = completionOrder(personaCount)
  const total = personas.length

  let cancelled = false
  let done = 0
  let timeoutId = null

  function emitNext(idx) {
    if (cancelled) return

    if (idx >= total) {
      onEvent({ type: 'synthesizing' })
      timeoutId = setTimeout(() => {
        if (cancelled) return
        // Built from this run's personas, not the module-level N=100
        // MOCK_SYNTHESIS, so the evidence ids it cites actually have
        // tiles in the grid at whatever N the slider is on.
        onEvent({ type: 'synthesis', data: buildMockSynthesis(personas) })
        onEvent({
          type: 'done',
          data: {
            stats: {
              mean_sentiment: average(reactions.map((r) => r.sentiment)),
              would_act_pct: percent(reactions, (r) => r.would_act),
              nano_calls: total,
              nano_ms_total: reactions.reduce((sum, r) => sum + r.latency_ms, 0),
              ultra_ms: 900,
            },
          },
        })
      }, SYNTHESIS_DELAY_MS)
      return
    }

    onEvent({ type: 'reaction', data: reactions[order[idx]] })
    done += 1
    if (done % 10 === 0 || done === total) {
      onEvent({ type: 'progress', data: { done, total } })
    }
    timeoutId = setTimeout(() => emitNext(idx + 1), REACTION_INTERVAL_MS)
  }

  timeoutId = setTimeout(() => emitNext(0), REACTION_INTERVAL_MS)

  return {
    personas,
    cancel() {
      cancelled = true
      if (timeoutId) clearTimeout(timeoutId)
    },
  }
}
