// Real fetch + SSE client. Swapped for mock.js behind ?mock=1.
//
// Both branches emit the same event shapes (reaction, progress,
// synthesizing, synthesis, done, cancelled, error) so the rest of the app
// never has to know which one it's talking to. The real branch is
// POST /run plus an EventSource on /run/{id}/stream; the mock branch
// synthesizes the same sequence on timers, with no network and no credits
// spent.

import { localPrecheck } from './precheck.js'
import {
  buildMockComparison,
  buildMockCompareResult,
  buildMockPersonas,
  buildMockReactions,
  buildMockReactionsB,
  buildMockStats,
  buildMockSynthesis,
  EXAMPLES,
} from './mock.js'

// Mock mode's pool is synthesized on demand (buildMockPersonas takes any
// n), sized to match the real 1,000-persona pool so mock mode exercises
// the same grid layout, filters and tile sizes at every N.
export const MOCK_POOL_SIZE = 1000

const REACTION_INTERVAL_MS = 80
const SYNTHESIS_DELAY_MS = 500

export function isMockMode() {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).has('mock')
}

// The real pool, fetched once and cached -- previewPersonas needs it
// synchronously (the grid re-lays out instantly as the slider moves,
// before Run is ever clicked), and a fresh fetch per call would both be
// slow and risk a different array each time.
let realPoolCache = null

async function loadRealPool() {
  if (realPoolCache) return realPoolCache
  const res = await fetch('/personas')
  if (!res.ok) throw new Error(`GET /personas failed: HTTP ${res.status}`)
  realPoolCache = await res.json()
  return realPoolCache
}

// Advisory pre-submission check (POST /check, one Nano call). Returns
// null in mock mode, which has no backend to ask -- the caller treats
// null as "no opinion" and runs.
//
// Keyed by the exact text so the verdict can be computed while the user
// is still reading their own paste and reused when they click Run. The
// check takes 5-10s, which is fine in the background and bad in front of
// a button press.
const checkCache = new Map()

export async function checkArtifact(text) {
  const key = (text ?? '').trim()
  if (!key) return null

  // Local pass first, in both modes: instant, free, and the only check
  // available in mock mode (which has no backend to ask). It only fires
  // on overwhelming signals, so when it does there is nothing a model
  // call would add.
  const local = localPrecheck(key)
  if (local) return local

  if (isMockMode()) return null
  if (checkCache.has(key)) return checkCache.get(key)

  const pending = (async () => {
    const res = await fetch('/check', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: key }),
    })
    if (!res.ok) return null // fails open, same as the server side
    return res.json()
  })()
  // Cache the promise, not the result: a debounced check and a Run click
  // racing on the same text should share one request, not make two.
  checkCache.set(key, pending)
  try {
    const verdict = await pending
    // A failed check (429, 5xx) is not a verdict: dropping it lets the
    // next Run click ask again instead of skipping the check for good.
    if (verdict === null) checkCache.delete(key)
    return verdict
  } catch {
    checkCache.delete(key)
    return null
  }
}

export async function getPoolSize() {
  if (isMockMode()) return MOCK_POOL_SIZE
  const pool = await loadRealPool()
  return pool.length
}

// Synchronous: lets the grid re-lay out to a new N the instant the slider
// moves, before Run is ever clicked. Real mode: the first N of the cached
// pool, in on-disk order -- the same rule panel.personas.sample_stable
// uses server-side, so the preview always shows the identities a real run
// will actually use.
export function previewPersonas(n) {
  if (isMockMode()) return buildMockPersonas(n)
  if (!realPoolCache) {
    throw new Error('persona pool not loaded yet -- call getPoolSize() first')
  }
  return realPoolCache.slice(0, n)
}

// previousReactionsById: only passed for a compare's second run (Run B),
// and only used in mock mode, where Run B is a deterministic edit of Run
// A's reactions (mock.js's buildMockReactionsB). The real backend reruns
// the panel on the edited text instead.
//
// text: the POST /run body in real mode; in mock mode it picks the
// per-example calibration and wording. label: real mode only.
//
// cached: real mode only. Appends ?cached=true, which the backend honors
// only when `text` matches one of the example files exactly.
//
// skipSynthesis: for a compare's Run B, whose report is never shown. The
// run streams its reactions and goes straight to `done`, saving the Ultra
// call (and, above 150 personas, the Super condensing pass).
//
// Returns a handle synchronously ({cancel()}), even though a real run
// needs an async POST before its run_id exists -- `cancelled` covers the
// gap, so a cancel issued before the POST resolves still reaches the
// server once the id is known.
export function startRun({
  text,
  url,
  label,
  personaCount,
  onEvent,
  previousReactionsById,
  cached,
  skipSynthesis,
}) {
  if (isMockMode()) return startMockRun({ personaCount, onEvent, previousReactionsById, text, skipSynthesis })
  return startRealRun({ text, url, label, personaCount, onEvent, cached, skipSynthesis })
}

// FastAPI's `detail` is a string for errors the app raises (429 limits,
// 400s) but a list of {msg, ...} objects for request validation (422).
function errorDetail(body, fallback) {
  const { detail } = body ?? {}
  if (typeof detail === 'string' && detail) return detail
  if (Array.isArray(detail) && detail.length) {
    return detail.map((d) => d?.msg ?? String(d)).join('; ')
  }
  return fallback
}

// The most recent real run this page started. A new run names it in
// `replaces`, so the server stops it before applying its one-active-run-
// per-client rule -- otherwise clicking Run again would race the previous
// run's fire-and-forget cancel and could be refused with a 429.
let lastRealRunId = null

function startRealRun({ text, url, label, personaCount, onEvent, cached = false, skipSynthesis = false }) {
  let cancelled = false
  let es = null
  let runId = null

  // Closing the EventSource only stops us listening; the server keeps
  // spending on every remaining call until it is told to stop.
  // Fire-and-forget: a failed cancel must not throw into the caller.
  function cancelOnServer() {
    fetch(`/run/${encodeURIComponent(runId)}/cancel`, { method: 'POST' }).catch(() => {})
  }

  ;(async () => {
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
          skip_synthesis: skipSynthesis || undefined,
          replaces: lastRealRunId || undefined,
        }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        onEvent({ type: 'error', data: { message: errorDetail(body, `POST /run failed: HTTP ${res.status}`) } })
        return
      }
      ;({ run_id: runId } = await res.json())
      lastRealRunId = runId
    } catch (e) {
      onEvent({ type: 'error', data: { message: String(e) } })
      return
    }
    if (cancelled) {
      cancelOnServer()
      return
    }

    // In URL mode the artifact's text only exists server-side (the
    // backend fetched and extracted it), but "Apply and compare" needs a
    // local copy to edit. One extra GET of the run record is cheaper than
    // widening POST /run's response.
    let fetchedText
    if (url) {
      try {
        const record = await fetch(`/run/${encodeURIComponent(runId)}`).then((r) => (r.ok ? r.json() : null))
        fetchedText = record?.artifact?.text
      } catch {
        // Non-fatal: the run itself is already underway. Apply-and-compare
        // stays disabled for this run rather than the run failing.
      }
    }

    // Not a server event -- a local-only signal so the caller (App.jsx)
    // can stash the run_id for POST /compare later. Mock mode never emits
    // this; real callers must handle it.
    if (cancelled) return
    onEvent({ type: 'run_started', data: { run_id: runId, artifact_text: fetchedText } })

    es = new EventSource(`/run/${encodeURIComponent(runId)}/stream`)
    // 'cancelled' is terminal like 'done' -- a deliberate stop, which the
    // server reports distinctly from an error so the UI never says
    // "something went wrong" about something the user asked for.
    for (const type of ['reaction', 'progress', 'synthesizing', 'synthesis', 'done', 'cancelled']) {
      es.addEventListener(type, (e) => {
        onEvent({ type, data: e.data ? JSON.parse(e.data) : undefined })
        if (type === 'done' || type === 'cancelled') es.close()
      })
    }
    es.addEventListener('error', (e) => {
      // Both the server's named "error" SSE event and the
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
      if (cancelled) return
      cancelled = true
      es?.close()
      // Before the POST resolves there is no id yet; the IIFE above sends
      // the cancel as soon as it learns one.
      if (runId) cancelOnServer()
    },
  }
}

// Optional server features (GET /features). Mock mode simulates the
// market comparison on timers, so it reports it available.
export async function getFeatures() {
  if (isMockMode()) return { market: true }
  const res = await fetch('/features')
  if (!res.ok) throw new Error(`GET /features failed: HTTP ${res.status}`)
  return res.json()
}

// The example buttons, loaded from GET /examples in the real client.
// Mock mode sources the same shape from mock.js.
export async function getExamples() {
  if (isMockMode()) return EXAMPLES
  const res = await fetch('/examples')
  if (!res.ok) throw new Error(`GET /examples failed: HTTP ${res.status}`)
  return res.json()
}

// "Apply and compare" result. Mock mode computes it from the two reaction
// sets the run actually produced, so it is correct at any N; the real
// branch POSTs /compare. personas/reactionsAById are mock-only (Run B is
// recomputed locally); runA/runB are real-only (the backend already holds
// both runs). Each branch ignores the args it doesn't need.
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
    throw new Error(errorDetail(body, `POST /compare failed: HTTP ${res.status}`))
  }
  return res.json()
}

// Deterministic Fisher-Yates permutation (no Math.random) so reactions
// arrive out of persona order, the same way the real fan-out's
// asyncio.as_completed does -- this is what exercises "tiles are placed by
// persona_id, not arrival order."
function completionOrder(n) {
  const order = Array.from({ length: n }, (_, i) => i)
  for (let i = n - 1; i > 0; i--) {
    const j = (i * 2654435761) % (i + 1)
    ;[order[i], order[j]] = [order[j], order[i]]
  }
  return order
}

function startMockRun({ personaCount, onEvent, previousReactionsById, text, skipSynthesis }) {
  const personas = buildMockPersonas(personaCount)
  const reactions = previousReactionsById
    ? buildMockReactionsB(personas, previousReactionsById)
    : buildMockReactions(personas, text)
  const order = completionOrder(personaCount)
  const total = personas.length

  let cancelled = false
  let done = 0
  let timeoutId = null

  function emitNext(idx) {
    if (cancelled) return

    if (idx >= total) {
      if (skipSynthesis) {
        onEvent({ type: 'done', data: { stats: buildMockStats(reactions) } })
        return
      }
      onEvent({ type: 'synthesizing' })
      timeoutId = setTimeout(() => {
        if (cancelled) return
        // Built from this run's personas and reactions, so the evidence ids
        // have tiles in the grid and the percentages are real counts at
        // whatever N the slider is on.
        onEvent({ type: 'synthesis', data: buildMockSynthesis(personas, text, reactions) })
        onEvent({ type: 'done', data: { stats: buildMockStats(reactions) } })
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
    cancel() {
      cancelled = true
      if (timeoutId) clearTimeout(timeoutId)
    },
  }
}


// --- Market benchmark -------------------------------------------------------

// Mock mode has no backend to search with, so the feature is real-mode
// only; the caller hides the button rather than offering something that
// cannot work.
// Mock mode walks the same event sequence on timers so the UI can be
// seen without a backend, a Tavily key or credits. It never claims to
// have read a real page -- see mock.js's buildMockComparison.
const MOCK_BENCHMARK_STEPS_MS = 900

function startMockBenchmark({ text, panelSize, onEvent }) {
  let cancelled = false
  const timers = []
  const at = (ms, fn) => timers.push(setTimeout(() => !cancelled && fn(), ms))
  const comparison = buildMockComparison(text, panelSize)

  at(0, () => onEvent({ type: 'started', data: { benchmark_id: 'mock' } }))
  at(MOCK_BENCHMARK_STEPS_MS * 0.3, () => onEvent({ type: 'searching' }))
  at(MOCK_BENCHMARK_STEPS_MS * 1, () =>
    onEvent({ type: 'query', data: { query: comparison.query } }))
  at(MOCK_BENCHMARK_STEPS_MS * 2, () =>
    onEvent({ type: 'pages', data: { pages: comparison.pages } }))
  comparison.pages.forEach((p, i) =>
    at(MOCK_BENCHMARK_STEPS_MS * (3 + i), () =>
      onEvent({ type: 'page_done', data: { label: p.label } })))
  at(MOCK_BENCHMARK_STEPS_MS * 7, () => onEvent({ type: 'comparing' }))
  at(MOCK_BENCHMARK_STEPS_MS * 8, () => {
    onEvent({ type: 'comparison', data: comparison })
    onEvent({ type: 'done' })
  })

  return {
    cancel() {
      cancelled = true
      timers.forEach(clearTimeout)
    },
  }
}

export function startBenchmark({ runId, text, panelSize, onEvent }) {
  if (isMockMode()) return startMockBenchmark({ text, panelSize, onEvent })
  let cancelled = false
  let es = null

  ;(async () => {
    let benchmarkId
    try {
      const res = await fetch('/benchmark', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ run_id: runId }),
      })
      if (!res.ok) {
        const body = await res.json().catch(() => ({}))
        onEvent({ type: 'error', data: { message: errorDetail(body, `POST /benchmark failed: HTTP ${res.status}`) } })
        return
      }
      ;({ benchmark_id: benchmarkId } = await res.json())
    } catch (e) {
      onEvent({ type: 'error', data: { message: String(e) } })
      return
    }
    if (cancelled) return
    onEvent({ type: 'started', data: { benchmark_id: benchmarkId } })

    es = new EventSource(`/benchmark/${benchmarkId}/stream`)
    const TERMINAL = ['done', 'no_pages', 'cancelled']
    for (const type of ['searching', 'query', 'pages', 'page_done', 'comparing', 'comparison', ...TERMINAL]) {
      es.addEventListener(type, (e) => {
        onEvent({ type, data: e.data ? JSON.parse(e.data) : undefined })
        if (TERMINAL.includes(type)) es.close()
      })
    }
    es.addEventListener('error', (e) => {
      // Same shape check as startRealRun: a server-sent "error" event is a
      // MessageEvent with .data; the browser's own failure is a bare Event.
      if (e.data !== undefined) {
        onEvent({ type: 'error', data: JSON.parse(e.data) })
        es.close()
      } else if (es.readyState === EventSource.CLOSED) {
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
