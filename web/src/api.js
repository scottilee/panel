// Real fetch + SSE client. Swapped for mock.js behind ?mock=1. Section 6.9.
//
// The real client (POST /run + EventSource on /run/{id}/stream) is stage 7.
// This file's mock branch emits the same event shapes stage 7's real client
// will (section 3's SSE event list: reaction, progress, synthesizing,
// synthesis, done, error) so the rest of the app never has to know which
// branch it's talking to.

import {
  buildMockCompareResult,
  buildMockPersonas,
  buildMockReactions,
  buildMockReactionsB,
  EXAMPLES,
  MOCK_SYNTHESIS,
} from './mock.js'

// Mock mode's pool is synthesized on demand (buildMockPersonas takes any n),
// so the slider can go well past the real ~100-persona pool to exercise
// grid layout at scale (section 6.2's N=1000 case) without touching the
// real data/personas.json pool size.
export const MOCK_POOL_SIZE = 1000

const REACTION_INTERVAL_MS = 80
const SYNTHESIS_DELAY_MS = 500

export function isMockMode() {
  if (typeof window === 'undefined') return false
  return new URLSearchParams(window.location.search).has('mock')
}

export async function getPoolSize() {
  if (isMockMode()) return MOCK_POOL_SIZE
  throw new Error('real /personas client not implemented yet (stage 7)')
}

// Synchronous: lets the grid re-lay out to a new N the instant the slider
// moves, before Run is ever clicked (section 6.10).
export function previewPersonas(n) {
  if (isMockMode()) return buildMockPersonas(n)
  throw new Error('real persona sampling not implemented yet (stage 7)')
}

// previousReactionsById: only passed for a compare's second run (Run B).
// Mock mode uses it to make Run B's reactions a deliberate, deterministic
// edit of Run A's actual data (see mock.js's buildMockReactionsB) --
// without it, Run B used to be generated the exact same way as Run A
// (buildMockReactions depends only on the persona list, never on the
// artifact text), so toggling Run A/Run B/Delta showed no visible change
// at all. Stage 7's real client passes a run_a id instead; the backend
// computes the real delta from genuinely different model output.
export function startRun({ personaCount, onEvent, previousReactionsById }) {
  if (isMockMode()) return startMockRun({ personaCount, onEvent, previousReactionsById })
  throw new Error('real backend client not implemented yet (stage 7)')
}

// Section 6.8: three example buttons, loaded from GET /examples in the
// real client (stage 8). Mock mode sources the same shape from mock.js.
export async function getExamples() {
  if (isMockMode()) return EXAMPLES
  throw new Error('real /examples client not implemented yet (stage 8)')
}

// Section 6.6's "Apply this edit and compare": mock mode computes the
// result from the two reaction sets the run actually produced (so it's
// correct at any N, and actually reflects a real Run A vs Run B
// difference) rather than a fixed, disconnected canned object. Still
// fast and synchronous -- no network, no delay -- matching section 6.9's
// "no real computation cost" intent, just not "hardcoded regardless of
// input". The real client (stage 7) will POST /compare and await the
// response.
export async function getCompareResult({ personas, reactionsAById }) {
  if (isMockMode()) {
    // buildMockReactionsB is deterministic (same personas + reactionsA
    // always produce the same Run B), so recomputing it here gives the
    // exact same reactions startMockRun already streamed into Run B's
    // live state -- no need to thread the accumulated state back in.
    const reactionsBById = {}
    for (const r of buildMockReactionsB(personas, reactionsAById)) reactionsBById[r.persona_id] = r
    return buildMockCompareResult(personas, reactionsAById, reactionsBById)
  }
  throw new Error('real /compare client not implemented yet (stage 7)')
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
        onEvent({ type: 'synthesis', data: MOCK_SYNTHESIS })
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
