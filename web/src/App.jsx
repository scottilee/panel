import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import { getCompareResult, getExamples, getPoolSize, previewPersonas, startRun } from './api.js'
import ArtifactInput, { MAX_TEXT_LENGTH, MIN_TEXT_LENGTH } from './components/ArtifactInput.jsx'
import CompareBar from './components/CompareBar.jsx'
import Filters from './components/Filters.jsx'
import Findings from './components/Findings.jsx'
import Grid from './components/Grid.jsx'
import PersonaCard from './components/PersonaCard.jsx'
import PersonaCountControl from './components/PersonaCountControl.jsx'
import StatsStrip from './components/StatsStrip.jsx'
import Verdict from './components/Verdict.jsx'
import {
  STATUS,
  initialState,
  reducer,
  segmentFilterToChips,
  selectCompareElapsedSeconds,
  selectCompareIsRunning,
  selectCompareLiveStats,
  selectCountLocked,
  selectDeltaByPersonaId,
  selectElapsedSeconds,
  selectFilteredPersonaIds,
  selectIsRunning,
  selectLiveStats,
} from './state.js'

const DEFAULT_PERSONA_COUNT = 100
const TICK_INTERVAL_MS = 250

// Applies highest_leverage_edit to the artifact text for mock-mode's
// Apply-and-compare flow. The real equivalent (panel/synthesize.py's
// apply_edit) also tolerates whitespace-flexible matches; mock data
// guarantees an exact substring match by construction, so a plain
// replace is enough here -- the fallback just warns instead of silently
// comparing against unedited text.
function applyEdit(artifactText, currentText, proposedText) {
  if (artifactText.includes(currentText)) {
    return artifactText.replace(currentText, proposedText)
  }
  console.warn('Findings: current_text not found verbatim in artifact; comparing against unedited text')
  return artifactText
}

// New per the redesign: "As they answered" / "Grouped by reaction" sorts
// the grid's tile positions by (would_act desc, sentiment desc). Purely a
// display-order concern -- reactions stay keyed by persona_id, so sorting
// this array never touches compare mode's apples-to-apples guarantee.
function sortByReaction(personas, reactionsById) {
  return [...personas].sort((a, b) => {
    const ra = reactionsById[a.id]
    const rb = reactionsById[b.id]
    const actA = ra?.would_act ? 1 : 0
    const actB = rb?.would_act ? 1 : 0
    if (actB !== actA) return actB - actA
    const sa = ra?.sentiment ?? -99
    const sb = rb?.sentiment ?? -99
    return sb - sa
  })
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const runHandleRef = useRef(null)
  const compareRunHandleRef = useRef(null)

  const [examples, setExamples] = useState([])

  // The pool fetch is load-bearing for the whole setup card: poolSize is
  // the N slider's maximum, and previewPersonas() throws outright until
  // the pool has landed. With no .catch() a failed GET /personas left the
  // slider pinned at initialState's placeholder 100 AND made every move
  // of it throw inside the change handler -- so the control looked frozen
  // with nothing on screen to say why. The usual cause is mundane (the
  // backend isn't running yet, or the dev server's proxy target is down),
  // which is exactly the case that deserves a readable message.
  useEffect(() => {
    getPoolSize()
      .then((poolSize) => {
        dispatch({ type: 'SET_POOL_SIZE', poolSize })
        const n = Math.min(DEFAULT_PERSONA_COUNT, poolSize)
        dispatch({ type: 'SET_PERSONA_COUNT', n, personas: previewPersonas(n) })
      })
      .catch((e) => {
        dispatch({
          type: 'ERROR',
          message: `Could not load the persona pool (${e.message}). Is the backend running on port 8000?`,
        })
      })
    getExamples()
      .then(setExamples)
      .catch(() => setExamples([]))
  }, [])

  const isRunning = selectIsRunning(state)
  const compareIsRunning = selectCompareIsRunning(state)
  const countLocked = selectCountLocked(state)

  // Elapsed-seconds ticker, section 6.4 ("elapsed stops" on done) -- one
  // ticker drives both run A's and compare's elapsed time (state.js's TICK
  // updates whichever has a startedAt).
  useEffect(() => {
    if (!isRunning && !compareIsRunning) return
    const id = setInterval(() => dispatch({ type: 'TICK' }), TICK_INTERVAL_MS)
    return () => clearInterval(id)
  }, [isRunning, compareIsRunning])

  useEffect(() => {
    return () => {
      runHandleRef.current?.cancel()
      compareRunHandleRef.current?.cancel()
    }
  }, [])

  // Pinned card, segment filters, evidence highlight, compare view mode,
  // tile order: all UI-only, not "run state" the way reactions/synthesis are.
  const [pinnedPersonaId, setPinnedPersonaId] = useState(null)
  const [activeFilters, setActiveFilters] = useState([])
  const [highlightedIds, setHighlightedIds] = useState(null)
  const [viewMode, setViewMode] = useState('A')
  const [sortedByReaction, setSortedByReaction] = useState(false)

  const handleTileClick = useCallback((persona) => {
    setPinnedPersonaId((id) => (id === persona.id ? null : persona.id))
  }, [])
  const handleBackgroundClick = useCallback(() => setPinnedPersonaId(null), [])
  const handleToggleFilter = useCallback((filter) => {
    setActiveFilters((filters) =>
      filters.some((f) => f.group === filter.group && f.value === filter.value)
        ? filters.filter((f) => !(f.group === filter.group && f.value === filter.value))
        : [...filters, filter],
    )
  }, [])
  const handleClearFilters = useCallback(() => setActiveFilters([]), [])
  // Age/income are range-style filters (section 6.5, changed 2026-10-29):
  // at most one entry per group, present only when narrowed from the full
  // default range -- `isDefault` removes the entry entirely rather than
  // storing a range that covers everyone, keeping anyFilter/matchTxt
  // correct without special-casing "range at full extent" everywhere else.
  const handleRangeFilterChange = useCallback((group, range, isDefault) => {
    setActiveFilters((filters) => {
      const withoutGroup = filters.filter((f) => f.group !== group)
      return isDefault ? withoutGroup : [...withoutGroup, { group, ...range }]
    })
  }, [])
  // Section 6.6: a finding's segment chip applies that filter outright
  // (replaces activeFilters) rather than toggling/merging -- it's "show
  // me this segment", not "add one more condition to what I already had".
  const handleSegmentClick = useCallback((clauses) => {
    setActiveFilters(segmentFilterToChips(clauses))
  }, [])
  const handleEvidenceHoverStart = useCallback((evidence) => setHighlightedIds(new Set(evidence)), [])
  const handleEvidenceHoverEnd = useCallback(() => setHighlightedIds(null), [])

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key === 'Escape') setPinnedPersonaId(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])

  const filteredIds = useMemo(
    () => selectFilteredPersonaIds(state.personas, activeFilters),
    [state.personas, activeFilters],
  )
  const pinnedPersona = useMemo(
    () => state.personas.find((p) => p.id === pinnedPersonaId) ?? null,
    [state.personas, pinnedPersonaId],
  )
  const deltaByPersonaId = useMemo(
    () => (viewMode === 'delta' ? selectDeltaByPersonaId(state) : null),
    [viewMode, state],
  )

  function handlePersonaCountChange(n) {
    if (isRunning || countLocked) return
    // previewPersonas throws until the pool has loaded, and an exception
    // here used to kill the whole change -- the slider would refuse to
    // move at all, with no clue that the real problem was a failed
    // GET /personas. The mount effect reports that failure now; this is
    // the matching guard so the control fails quietly rather than
    // throwing on every drag.
    let personas
    try {
      personas = previewPersonas(n)
    } catch {
      return
    }
    runHandleRef.current?.cancel()
    dispatch({ type: 'SET_PERSONA_COUNT', n, personas })
    setPinnedPersonaId(null)
    setActiveFilters([])
    setViewMode('A')
    setSortedByReaction(false)
  }

  function handleSelectExample(example) {
    if (isRunning) return
    dispatch({ type: 'SET_ARTIFACT_TEXT', text: example.text })
    dispatch({ type: 'SET_LABEL', label: example.label })
  }

  // Section 6.8: "if a live run emits error within 10 seconds of
  // starting, the frontend silently re-requests the same example with
  // ?cached=true... never show a raw error to a judge; show 'Retrying...'
  // then the cached result." allowRetry is false on the retry's own
  // handler so a failing cached replay (e.g. no cache file for this
  // example) surfaces as a real error instead of looping.
  function makeRunEventHandler({ startedAt, allowRetry }) {
    return (event) => {
      switch (event.type) {
        case 'run_started':
          dispatch({ type: 'SET_RUN_ID', runId: event.data.run_id })
          // URL mode only: the text the server extracted from the page,
          // so the local copy matches what the panel actually read and
          // "Apply and compare" has something to edit.
          if (event.data.artifact_text) {
            dispatch({ type: 'SET_ARTIFACT_TEXT', text: event.data.artifact_text })
          }
          break
        case 'reaction':
          dispatch({ type: 'SET_RETRYING', retrying: false })
          dispatch({ type: 'REACTION', reaction: event.data })
          break
        case 'progress':
          dispatch({ type: 'PROGRESS', progress: event.data })
          break
        case 'synthesizing':
          dispatch({ type: 'SYNTHESIZING' })
          break
        case 'synthesis':
          dispatch({ type: 'SYNTHESIS', synthesis: event.data })
          break
        case 'done':
          dispatch({ type: 'DONE', stats: event.data.stats })
          break
        case 'error': {
          const withinRetryWindow = Date.now() - startedAt < 10_000
          const matchedExample = examples.find((ex) => ex.text === state.artifactText)
          if (allowRetry && withinRetryWindow && matchedExample) {
            runRealOrCachedExample(matchedExample)
          } else {
            dispatch({ type: 'SET_RETRYING', retrying: false })
            dispatch({ type: 'ERROR', message: event.data?.message ?? 'unknown error' })
          }
          break
        }
        default:
          break
      }
    }
  }

  // cached: true replays section 5.8's known-good cached result instead
  // of a real run -- used both for the 6.8 auto-retry above and (if ever
  // wired to a UI control) a direct "run cached" path.
  function runRealOrCachedExample(example, cached = true) {
    dispatch({ type: 'SET_RETRYING', retrying: cached })
    runHandleRef.current?.cancel()
    const startedAt = Date.now()
    runHandleRef.current = startRun({
      text: example.text,
      label: example.label,
      personaCount: state.personaCount,
      cached,
      onEvent: makeRunEventHandler({ startedAt, allowRetry: false }),
    })
  }

  function handleRun() {
    runHandleRef.current?.cancel()
    compareRunHandleRef.current?.cancel()
    setViewMode('A')
    setSortedByReaction(false)
    dispatch({ type: 'RUN_START' })
    const startedAt = Date.now()
    runHandleRef.current = startRun({
      // In URL mode the server fetches and extracts the text itself
      // (POST /run accepts text XOR url). This used to send
      // state.artifactText unconditionally, which in URL mode is empty --
      // so every URL-mode run was a 422 and the whole Text/URL toggle was
      // dead in real mode.
      text: state.inputMode === 'url' ? undefined : state.artifactText,
      url: state.inputMode === 'url' ? state.artifactUrl.trim() : undefined,
      label: state.label,
      personaCount: state.personaCount,
      onEvent: makeRunEventHandler({ startedAt, allowRetry: true }),
    })
  }

  // Section 6.6: writes proposed_text over current_text into the second
  // textarea and triggers compare immediately -- both runs of a compare
  // use the same N (section 6.10), so this reuses state.personaCount/
  // state.personas as-is rather than resampling.
  function handleApplyAndCompare() {
    if (!state.synthesis) return
    const edit = state.synthesis.highest_leverage_edit
    const editedText = applyEdit(state.artifactText, edit.current_text, edit.proposed_text)
    dispatch({ type: 'COMPARE_SET_TEXT', text: editedText })
    runCompare(editedText)
  }

  function runCompare(text) {
    compareRunHandleRef.current?.cancel()
    dispatch({ type: 'COMPARE_RUN_START' })
    // Bug fixed 2026-10-29: without previousReactionsById, mock Run B used
    // to generate reactions purely from the persona list (same as Run A),
    // so Run A/Run B/Delta were always identical -- toggling the view
    // visibly did nothing. previousReactionsById makes mock Run B a
    // deliberate, deterministic edit of Run A's real reactions instead.
    // Ignored entirely by the real client (section 6.9's getCompareResult
    // docstring) -- it computes a genuine delta from genuine model calls.
    const reactionsAById = state.reactionsById
    const personas = state.personas
    const runIdA = state.runId
    // Captured locally rather than read back from state.compare.runId in
    // the 'done' handler below: that avoids depending on this closure's
    // dispatches having already been applied to the next render's state
    // by the time 'done' fires, which isn't guaranteed.
    let runIdB = null
    compareRunHandleRef.current = startRun({
      text,
      label: state.label,
      personaCount: state.personaCount,
      previousReactionsById: reactionsAById,
      onEvent: (event) => {
        switch (event.type) {
          case 'run_started':
            runIdB = event.data.run_id
            dispatch({ type: 'COMPARE_SET_RUN_ID', runId: runIdB })
            break
          case 'reaction':
            dispatch({ type: 'COMPARE_REACTION', reaction: event.data })
            break
          case 'progress':
            dispatch({ type: 'COMPARE_PROGRESS', progress: event.data })
            break
          case 'done':
            dispatch({ type: 'COMPARE_DONE', stats: event.data.stats })
            getCompareResult({ personas, reactionsAById, runA: runIdA, runB: runIdB }).then((result) => {
              dispatch({ type: 'COMPARE_RESULT', result })
              setViewMode('delta')
            })
            break
          case 'error':
            dispatch({ type: 'COMPARE_ERROR', message: event.data?.message ?? 'unknown error' })
            break
          default:
            break
        }
      },
    })
  }

  // Which reaction set currently drives the grid/stats, per the compare
  // view toggle (section 6.7) -- switching views never re-fetches, it
  // just changes which already-in-memory data is being looked at.
  const gridReactionsById = viewMode === 'A' ? state.reactionsById : state.compare.reactionsById
  const liveStats = viewMode === 'A' ? selectLiveStats(state, filteredIds) : selectCompareLiveStats(state)
  const elapsedSeconds = viewMode === 'A' ? selectElapsedSeconds(state) : selectCompareElapsedSeconds(state)
  const statsStripStatus = viewMode === 'A' ? state.status : state.compare.status

  const displayPersonas = useMemo(
    () => (sortedByReaction ? sortByReaction(state.personas, gridReactionsById) : state.personas),
    [sortedByReaction, state.personas, gridReactionsById],
  )

  const canRun =
    !isRunning &&
    (state.inputMode === 'text'
      ? state.artifactText.trim().length >= MIN_TEXT_LENGTH && state.artifactText.length <= MAX_TEXT_LENGTH
      : state.artifactUrl.trim().length > 0)

  const anyRunStarted = state.status !== STATUS.IDLE
  const total = state.personas.length
  const doneCount = Object.keys(state.reactionsById).length

  let statusText
  if (!anyRunStarted) {
    statusText = `${total} people ready to read`
  } else if (state.status === STATUS.PENDING || state.status === STATUS.STREAMING || state.status === STATUS.SYNTHESIZING) {
    statusText = `Reading... ${doneCount} of ${total}`
  } else if (viewMode === 'delta' && state.compare.result) {
    statusText = `${state.compare.result.flipped_positive} people changed their mind`
  } else {
    statusText = `${doneCount} of ${total} have answered`
  }

  return (
    <div className="app-shell">
      <div className="app-column app-column-left">
        <h1 className="app-title">Panel</h1>
        <aside aria-label="Set up a run" className="setup-card">
          <div className="setup-logo">
            <span className="logo-badge">P</span>
            <span className="logo-word">Panel</span>
          </div>
          <ArtifactInput
            mode={state.inputMode}
            text={state.artifactText}
            url={state.artifactUrl}
            label={state.label}
            isRunning={isRunning}
            examples={examples}
            onModeChange={(mode) => dispatch({ type: 'SET_INPUT_MODE', mode })}
            onTextChange={(text) => dispatch({ type: 'SET_ARTIFACT_TEXT', text })}
            onUrlChange={(url) => dispatch({ type: 'SET_ARTIFACT_URL', url })}
            onLabelChange={(label) => dispatch({ type: 'SET_LABEL', label })}
            onSelectExample={handleSelectExample}
          />
          <PersonaCountControl
            n={state.personaCount}
            poolSize={state.poolSize}
            onChange={handlePersonaCountChange}
            disabled={isRunning || countLocked}
          />
          <div className="setup-step">
            <div className="step-header">
              <span className="step-num">3</span>
              <span className="step-label">Run it</span>
            </div>
            <button type="button" className="run-button" disabled={!canRun} onClick={handleRun}>
              {isRunning ? 'Running...' : anyRunStarted ? 'Run again' : 'Run panel'}
            </button>
          </div>
        </aside>
        {state.compare.visible && (
          <div className="compare-textarea-block">
            <div className="edit-label">Compare artifact (locked N: {state.personaCount})</div>
            <textarea className="artifact-textarea" value={state.compare.text} readOnly />
          </div>
        )}
        <Filters
          activeFilters={activeFilters}
          onToggle={handleToggleFilter}
          onRangeChange={handleRangeFilterChange}
          onClearAll={handleClearFilters}
          matchCount={filteredIds ? filteredIds.size : state.personas.length}
          totalCount={state.personas.length}
        />
      </div>
      <div className="app-column app-column-right">
        <Verdict
          synthesis={state.synthesis}
          runLabel={state.label || 'Untitled run'}
          wouldActPct={liveStats.wouldActPct}
          total={liveStats.done}
          onApplyAndCompare={handleApplyAndCompare}
          applyDisabled={state.compare.visible}
        />

        <section aria-label="Reactions" className="reactions-card">
          <div className="reactions-header">
            <div className="reactions-sub">
              <span className="reactions-sub-label">{anyRunStarted ? state.label || 'This run' : 'Not run yet'}</span>
              <span className="status-text">{statusText}</span>
            </div>
            {state.status === STATUS.DONE && (
              <div className="reactions-toggles">
                <div className="pill-toggle" role="group" aria-label="Order tiles">
                  <button
                    type="button"
                    className={!sortedByReaction ? 'active' : ''}
                    aria-pressed={!sortedByReaction}
                    onClick={() => setSortedByReaction(false)}
                  >
                    As they answered
                  </button>
                  <button
                    type="button"
                    className={sortedByReaction ? 'active' : ''}
                    aria-pressed={sortedByReaction}
                    onClick={() => setSortedByReaction(true)}
                  >
                    Grouped by reaction
                  </button>
                </div>
              </div>
            )}
          </div>

          <StatsStrip liveStats={liveStats} elapsedSeconds={elapsedSeconds} status={statsStripStatus} />

          <Grid
            personas={displayPersonas}
            reactionsById={gridReactionsById}
            filteredIds={filteredIds}
            highlightedIds={highlightedIds}
            deltaByPersonaId={deltaByPersonaId}
            onTileClick={handleTileClick}
            onBackgroundClick={handleBackgroundClick}
          />

          {!anyRunStarted ? (
            <span className="not-run-hint">Each square is one person. Click any of them to meet them before you run.</span>
          ) : (
            <div className="legend-row">
              <span className="legend-item">
                <span className="legend-swatch" style={{ background: 'var(--sentiment-neg2)' }} />
                Against
              </span>
              <span className="legend-item">
                <span className="legend-swatch" style={{ background: 'var(--sentiment-0)' }} />
                On the fence
              </span>
              <span className="legend-item">
                <span className="legend-swatch" style={{ background: 'var(--sentiment-pos2)' }} />
                For it
              </span>
              <span className="legend-item">
                <span className="legend-swatch" style={{ background: 'var(--sentiment-pos2)' }}>
                  <span className="legend-swatch-dot" />
                </span>
                Would act
              </span>
            </div>
          )}
        </section>

        {state.compare.result && (
          <CompareBar
            compareResult={state.compare.result}
            viewMode={viewMode}
            onSetViewMode={setViewMode}
            statsA={selectLiveStats(state)}
            statsB={selectCompareLiveStats(state)}
          />
        )}

        <Findings
          synthesis={state.synthesis}
          onSegmentClick={handleSegmentClick}
          onEvidenceHoverStart={handleEvidenceHoverStart}
          onEvidenceHoverEnd={handleEvidenceHoverEnd}
        />
      </div>
      {pinnedPersona && (
        <PersonaCard
          persona={pinnedPersona}
          reaction={gridReactionsById[pinnedPersona.id]}
          onClose={handleBackgroundClick}
        />
      )}
    </div>
  )
}
