import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'
import {
  checkArtifact,
  getCompareResult,
  getExamples,
  getFeatures,
  getPoolSize,
  isMockMode,
  previewPersonas,
  startBenchmark,
  startRun,
} from './api.js'
import ArtifactInput, { MAX_TEXT_LENGTH, MIN_TEXT_LENGTH } from './components/ArtifactInput.jsx'
import CompareBar from './components/CompareBar.jsx'
import Filters from './components/Filters.jsx'
import Findings from './components/Findings.jsx'
import MarketPanel from './components/MarketPanel.jsx'
import Grid from './components/Grid.jsx'
import PersonaCard from './components/PersonaCard.jsx'
import FastModeToggle from './components/FastModeToggle.jsx'
import PersonaCountControl from './components/PersonaCountControl.jsx'
import StatsStrip from './components/StatsStrip.jsx'
import Verdict from './components/Verdict.jsx'
import { applyEdit } from './edit.js'
import {
  STATUS,
  initialState,
  reducer,
  segmentFilter,
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
// Long enough that typing a sentence doesn't fire a call per keystroke,
// short enough that a paste-then-read user has the verdict before they
// reach for Run.
const CHECK_DEBOUNCE_MS = 1200

// "As they answered" / "Grouped by reaction" sorts
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
  // Optional server features; the market comparison needs a Tavily key.
  const [features, setFeatures] = useState({ market: false })

  // The pool fetch is load-bearing for the whole setup card: poolSize is
  // the N slider's maximum, and previewPersonas() throws until the pool
  // has landed. A failure has to say so on screen, or the slider just
  // looks frozen.
  useEffect(() => {
    getPoolSize()
      .then((poolSize) => {
        dispatch({ type: 'SET_POOL_SIZE', poolSize })
        const n = Math.min(DEFAULT_PERSONA_COUNT, poolSize)
        dispatch({ type: 'SET_PERSONA_COUNT', n, personas: previewPersonas(n) })
      })
      .catch((e) => {
        const devHint = import.meta.env.DEV ? ` (${e.message}; is the backend running on port 8000?)` : ''
        dispatch({
          type: 'ERROR',
          message: `Could not load the persona pool. Please refresh in a moment.${devHint}`,
        })
      })
    getExamples()
      .then(setExamples)
      .catch(() => setExamples([]))
    getFeatures()
      .then(setFeatures)
      .catch(() => setFeatures({ market: false }))
  }, [])

  const isRunning = selectIsRunning(state)
  const compareIsRunning = selectCompareIsRunning(state)
  const countLocked = selectCountLocked(state)

  // Elapsed-seconds ticker; stops on done. One ticker drives both run A's
  // and compare's elapsed time (state.js's TICK updates whichever has a
  // startedAt).
  useEffect(() => {
    if (!isRunning && !compareIsRunning) return
    const id = setInterval(() => dispatch({ type: 'TICK' }), TICK_INTERVAL_MS)
    return () => clearInterval(id)
  }, [isRunning, compareIsRunning])

  useEffect(() => {
    return () => {
      runHandleRef.current?.cancel()
      compareRunHandleRef.current?.cancel()
      marketHandleRef.current?.cancel()
    }
  }, [])

  // Pinned card, segment filters, evidence highlight, compare view mode,
  // tile order: all UI-only, not "run state" the way reactions/synthesis are.
  const [pinnedPersonaId, setPinnedPersonaId] = useState(null)
  const [activeFilters, setActiveFilters] = useState([])
  // Pre-submission check: the advisory verdict for the current text, and
  // the "are you sure" prompt it can raise. Advisory only -- see
  // panel/classify.py. Both are stored with the text they were computed
  // for and ignored once the text changes, so editing clears them without
  // a reset effect.
  const [textCheckEntry, setTextCheckEntry] = useState(null)
  const [confirmCheckEntry, setConfirmCheckEntry] = useState(null)
  // True only while Run is waiting on a check it had to make itself.
  const [checking, setChecking] = useState(false)
  const [applyError, setApplyError] = useState(null)
  const [confirmCancel, setConfirmCancel] = useState(false)
  // Market benchmark: null until started, then a status string plus
  // whatever has streamed in so far.
  const [market, setMarket] = useState(null)
  const marketHandleRef = useRef(null)
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
  // Age/income are range-style filters: at most one entry per group, present only when narrowed from the full
  // default range -- `isDefault` removes the entry entirely rather than
  // storing a range that covers everyone, keeping anyFilter/matchTxt
  // correct without special-casing "range at full extent" everywhere else.
  const handleRangeFilterChange = useCallback((group, range, isDefault) => {
    setActiveFilters((filters) => {
      const withoutGroup = filters.filter((f) => f.group !== group)
      return isDefault ? withoutGroup : [...withoutGroup, { group, ...range }]
    })
  }, [])
  // A finding's segment chip replaces activeFilters rather than merging --
  // it's "show me this segment", not "add one more condition" -- and is
  // applied exactly (state.js's segmentFilter), so the grid shows the same
  // people the finding's segment_size counted.
  const handleSegmentClick = useCallback((finding) => {
    setActiveFilters([segmentFilter(finding)])
  }, [])
  const handleRemoveSegment = useCallback(() => {
    setActiveFilters((filters) => filters.filter((f) => f.group !== 'segment'))
  }, [])
  const handleEvidenceHoverStart = useCallback((evidence) => setHighlightedIds(new Set(evidence)), [])
  const handleEvidenceHoverEnd = useCallback(() => setHighlightedIds(null), [])

  // Check the text while the user is still looking at it, not after they
  // click Run: the call takes 5-10s, which is invisible here and painful
  // in front of a button press. Debounced so typing doesn't fire a call
  // per keystroke, and api.js caches by exact text so this and the Run
  // click share one request.
  const checkableText = state.inputMode === 'text' ? state.artifactText.trim() : ''
  const textCheck = textCheckEntry?.text === checkableText ? textCheckEntry.verdict : null
  const confirmCheck = confirmCheckEntry?.text === checkableText ? confirmCheckEntry.verdict : null
  useEffect(() => {
    if (checkableText.length < MIN_TEXT_LENGTH || checkableText.length > MAX_TEXT_LENGTH) return
    let cancelled = false
    const id = setTimeout(() => {
      checkArtifact(checkableText).then((verdict) => {
        if (!cancelled) setTextCheckEntry({ text: checkableText, verdict })
      })
    }, CHECK_DEBOUNCE_MS)
    return () => {
      cancelled = true
      clearTimeout(id)
    }
  }, [checkableText])

  useEffect(() => {
    function onKeyDown(e) {
      if (e.key !== 'Escape') return
      // Escape dismisses the cancel prompt without cancelling -- the
      // safe direction for a destructive confirm.
      if (confirmCancel) {
        setConfirmCancel(false)
        return
      }
      setPinnedPersonaId(null)
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [confirmCancel])

  const filteredIds = useMemo(
    () => selectFilteredPersonaIds(state.personas, activeFilters),
    [state.personas, activeFilters],
  )
  const pinnedPersona = useMemo(
    () => state.personas.find((p) => p.id === pinnedPersonaId) ?? null,
    [state.personas, pinnedPersonaId],
  )
  // Which people answered differently in Run B, and how many. Used both
  // for the Run B status line and to outline those tiles, so the A/B
  // toggle shows *which* readers moved rather than only that some did.
  const changedIdsFromA = useMemo(() => {
    const a = state.reactionsById
    const b = state.compare.reactionsById
    const changed = new Set()
    for (const [id, after] of Object.entries(b)) {
      const before = a[id]
      if (!before || !after) continue
      if (before.sentiment !== after.sentiment || before.would_act !== after.would_act) {
        changed.add(id)
      }
    }
    return changed
  }, [state.reactionsById, state.compare.reactionsById])
  const changedFromA = changedIdsFromA.size

  // In Run B, ring the people who answered differently from Run A --
  // reusing the evidence-hover ring, which is already the right affordance
  // for "these specific ones". Hovering a finding's evidence still wins
  // while it is active.
  const ringedIds =
    highlightedIds ?? (viewMode === 'B' && state.compare.result ? changedIdsFromA : null)

  const deltaByPersonaId = useMemo(
    () => (viewMode === 'delta' ? selectDeltaByPersonaId(state) : null),
    [viewMode, state],
  )

  function handlePersonaCountChange(n) {
    if (isRunning || countLocked) return
    // previewPersonas throws until the pool has loaded. The mount effect
    // reports that failure; this guard keeps every drag from throwing.
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
    // One action, not two: SET_ARTIFACT_TEXT clears a borrowed label, so
    // dispatching them separately would set the name and immediately
    // throw it away.
    dispatch({ type: 'SELECT_EXAMPLE', text: example.text, label: example.label })
  }

  // Demo fallback: if a live run of one of the examples errors within 10
  // seconds, re-request it with ?cached=true -- but only at the one panel
  // size its recording covers, and never silently: the page says the
  // live run failed and why, and labels what follows as a recorded run.
  // allowRetry is false on the retry's own handler so a failing cached
  // replay surfaces as a real error instead of looping. `text` is what
  // this run was started with, not whatever the textarea holds by now.
  function makeRunEventHandler({ startedAt, allowRetry, text }) {
    return (event) => {
      switch (event.type) {
        case 'run_started':
          dispatch({ type: 'SET_RUN_ID', runId: event.data.run_id })
          // URL mode only: the text the server extracted from the page,
          // so the local copy matches what the panel actually read and
          // "Apply and compare" has something to edit.
          if (event.data.artifact_text) {
            dispatch({ type: 'SET_ARTIFACT_TEXT', text: event.data.artifact_text })
            dispatch({ type: 'SET_RUN_TEXT', text: event.data.artifact_text })
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
        case 'cancelled':
          dispatch({ type: 'CANCELLED' })
          break
        case 'error': {
          const withinRetryWindow = Date.now() - startedAt < 10_000
          const matchedExample = examples.find(
            (ex) => ex.text === text && ex.cached_persona_count === state.personaCount,
          )
          if (allowRetry && withinRetryWindow && matchedExample) {
            runCachedExample(matchedExample, event.data?.message ?? 'unknown error')
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

  // Replays the server's known-good cached result for an example instead
  // of a real run (the auto-retry above).
  function runCachedExample(example, reason) {
    dispatch({ type: 'CACHED_REPLAY', reason })
    runHandleRef.current?.cancel()
    const startedAt = Date.now()
    runHandleRef.current = startRun({
      text: example.text,
      label: example.label,
      personaCount: state.personaCount,
      cached: true,
      onEvent: makeRunEventHandler({ startedAt, allowRetry: false, text: example.text }),
    })
  }

  // Clicking Run consults the pre-submission check first. Three cases,
  // and the ordering matters: a verdict already in hand gates
  // immediately; a check still in flight is awaited (api.js dedupes, so
  // this joins the debounced request rather than making a second); and no
  // verdict at all -- mock mode, a failed check, URL mode -- just runs.
  // The check can only ever ask; it never refuses.
  async function handleRun() {
    if (state.inputMode === 'text' && !confirmCheck) {
      // The debounced check above usually has an answer ready. When it
      // doesn't -- Run clicked while still typing, or straight after a
      // paste -- this is a model call, so the button has to say it is
      // busy. Without `checking` the click looked ignored for several
      // seconds and the natural response was to click it again.
      let verdict = textCheck
      if (!verdict) {
        setChecking(true)
        try {
          verdict = await checkArtifact(checkableText)
        } finally {
          setChecking(false)
        }
      }
      if (verdict && verdict.is_reviewable === false) {
        setConfirmCheckEntry({ text: checkableText, verdict })
        return
      }
    }
    setConfirmCheckEntry(null)
    startRunNow()
  }

  function handleStartBenchmark() {
    // Mock mode has no run id (it never emits run_started), so the guard
    // is mode-aware rather than simply requiring one.
    if (!state.runId && !isMockMode()) return
    marketHandleRef.current?.cancel()
    setMarket({ status: 'pending', query: '', pages: [], pagesDone: [], comparison: null, error: null })
    marketHandleRef.current = startBenchmark({
      runId: state.runId,
      text: state.artifactText,
      panelSize: state.personaCount,
      onEvent: (event) => {
        setMarket((m) => {
          if (!m) return m
          switch (event.type) {
            case 'searching':
              return { ...m, status: 'searching' }
            case 'query':
              return { ...m, status: 'searching', query: event.data.query }
            case 'pages':
              return { ...m, status: 'reading', pages: event.data.pages }
            // Reading three pages takes minutes. Without this the card
            // shows one unchanging line for all of it and reads as hung.
            case 'page_done':
              return m.pagesDone.includes(event.data.label)
                ? m
                : { ...m, pagesDone: [...m.pagesDone, event.data.label] }
            case 'comparing':
              return { ...m, status: 'comparing' }
            case 'comparison':
              return { ...m, comparison: event.data }
            case 'done':
              return { ...m, status: 'done' }
            case 'no_pages':
              return { ...m, status: 'no_pages' }
            // Pages were found and read; too few readers answered on
            // them. Carries its own message because the reason differs.
            case 'pages_unusable':
              return { ...m, status: 'pages_unusable', error: event.data?.message ?? null }
            // Without this the status stays 'reading', which keeps
            // marketBusy true and so leaves Run disabled for good.
            case 'cancelled':
              return { ...m, status: 'cancelled' }
            case 'error':
              return { ...m, status: 'error', error: event.data?.message ?? 'unknown error' }
            default:
              return m
          }
        })
      },
    })
  }

  function handleDismissMarket() {
    marketHandleRef.current?.cancel()
    setMarket(null)
  }

  function handleCancelConfirmed() {
    setConfirmCancel(false)
    // Cancels locally AND server-side (api.js's handle) -- the credits
    // stop, which is the only version of this button worth having. The
    // handle's cancel stops its events, so each run's state is updated
    // here; Run A's is left alone when only the compare run was going.
    runHandleRef.current?.cancel()
    compareRunHandleRef.current?.cancel()
    if (isRunning) dispatch({ type: 'CANCELLED' })
    if (compareIsRunning) dispatch({ type: 'COMPARE_ERROR', message: 'The compare run was cancelled.' })
  }

  function startRunNow() {
    runHandleRef.current?.cancel()
    compareRunHandleRef.current?.cancel()
    setViewMode('A')
    setSortedByReaction(false)
    setApplyError(null)
    // In URL mode the server fetches and extracts the text itself, and
    // POST /run accepts exactly one of text or url; run_started reports
    // the extracted text back as runText.
    const text = state.inputMode === 'url' ? undefined : state.artifactText
    dispatch({ type: 'RUN_START', text, fast: state.fastMode })
    const startedAt = Date.now()
    runHandleRef.current = startRun({
      text,
      url: state.inputMode === 'url' ? state.artifactUrl.trim() : undefined,
      label: state.label,
      personaCount: state.personaCount,
      fast: state.fastMode,
      onEvent: makeRunEventHandler({ startedAt, allowRetry: true, text }),
    })
  }

  // Writes proposed_text over current_text in the text Run A read (not
  // the textarea, which may have been edited since) into the second
  // textarea and
  // starts the compare immediately. Both runs of a compare use the same
  // N, so this reuses state.personaCount/state.personas as-is rather than
  // resampling. If the phrase can't be found the compare doesn't start:
  // rerunning the unedited copy would report ~0 flips as if the edit had
  // failed.
  function handleApplyAndCompare() {
    if (!state.synthesis) return
    const edit = state.synthesis.highest_leverage_edit
    const editedText = applyEdit(state.runText, edit.current_text, edit.proposed_text)
    if (editedText === null) {
      setApplyError(
        "Couldn't find the phrase this edit replaces in your copy, so there is nothing to compare. " +
          'Make the change by hand and run again.',
      )
      return
    }
    setApplyError(null)
    dispatch({ type: 'COMPARE_SET_TEXT', text: editedText })
    runCompare(editedText)
  }

  function runCompare(text) {
    compareRunHandleRef.current?.cancel()
    dispatch({ type: 'COMPARE_RUN_START' })
    // previousReactionsById makes mock Run B a deterministic edit of Run
    // A's reactions, so the Run A/Run B/Delta views actually differ. The
    // real client ignores it -- it reruns the panel on the edited text.
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
      // Run B's own report is never shown, only its reactions.
      skipSynthesis: true,
      // Read the way Run A was, or the mode change would show up as flips.
      fast: state.runFast,
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
            getCompareResult({ personas, reactionsAById, runA: runIdA, runB: runIdB })
              .then((result) => {
                dispatch({ type: 'COMPARE_RESULT', result })
                setViewMode('delta')
              })
              .catch((e) => dispatch({ type: 'COMPARE_ERROR', message: e.message }))
            break
          case 'cancelled':
            dispatch({ type: 'COMPARE_ERROR', message: 'The compare run was cancelled.' })
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
  // view toggle -- switching views never re-fetches, it just changes which
  // already-in-memory data is being looked at.
  const gridReactionsById = viewMode === 'A' ? state.reactionsById : state.compare.reactionsById
  // Run A's stats are kept separately: the verdict belongs to Run A, so its
  // "N of M aren't convinced" must not switch to Run B with the view.
  const statsA = selectLiveStats(state, filteredIds)
  const answeredA = filteredIds ? selectLiveStats(state).usable : statsA.usable
  const liveStats = viewMode === 'A' ? statsA : selectCompareLiveStats(state, filteredIds)
  const elapsedSeconds = viewMode === 'A' ? selectElapsedSeconds(state) : selectCompareElapsedSeconds(state)
  const statsStripStatus = viewMode === 'A' ? state.status : state.compare.status
  const runStats = viewMode === 'A' ? state.stats : state.compare.stats

  const displayPersonas = useMemo(
    () => (sortedByReaction ? sortByReaction(state.personas, gridReactionsById) : state.personas),
    [sortedByReaction, state.personas, gridReactionsById],
  )

  // The server allows one run or market comparison per client at a time,
  // so Run waits for a comparison in progress rather than being refused.
  const marketBusy = ['pending', 'searching', 'reading', 'comparing'].includes(market?.status)
  const canRun =
    !isRunning &&
    !checking &&
    !marketBusy &&
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
    const { flipped_positive: yes, flipped_negative: no } = state.compare.result
    statusText = `${yes} moved to yes, ${no} moved to no`
  } else if (viewMode === 'B' && state.compare.result) {
    // One edit moves a handful of people out of hundreds -- that is the
    // finding, not a bug -- so switching to Run B recolours about 6% of
    // the grid and otherwise looks inert. Saying how many changed turns
    // a toggle that reads as broken into a number worth reading.
    statusText =
      changedFromA === 1
        ? `1 of ${total} read it differently than in Run A`
        : `${changedFromA} of ${total} read it differently than in Run A`
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
            fast={state.fastMode}
          />
          <div className="setup-step">
            <div className="step-header">
              <span className="step-num">3</span>
              <span className="step-label">Run it</span>
            </div>
            <FastModeToggle
              fast={state.fastMode}
              onChange={(fast) => dispatch({ type: 'SET_FAST_MODE', fast })}
              disabled={isRunning}
              mock={isMockMode()}
            />
            <button type="button" className="run-button" disabled={!canRun} onClick={handleRun}>
              {(isRunning || checking) && <span className="run-spinner" aria-hidden="true" />}
              {isRunning
                ? 'Running'
                : checking
                  ? 'Checking'
                  : anyRunStarted
                    ? 'Run again'
                    : 'Run panel'}
            </button>
            {(isRunning || compareIsRunning) && (
              <button
                type="button"
                className="cancel-button"
                onClick={() => setConfirmCancel(true)}
              >
                {isRunning ? 'Cancel run' : 'Cancel compare run'}
              </button>
            )}
            {/* The one place run errors, retries and cancellation
                surface. A failed run must say so rather than stopping
                silently with the grid half-full. */}
            {state.retrying && state.cachedReplay && (
              <p className="run-note run-note-busy">
                The live run failed ({state.cachedReplay.reason}). Loading a recorded run of this example instead...
              </p>
            )}
            {!state.retrying && state.cachedReplay && state.status !== STATUS.ERROR && (
              <p className="run-note run-note-replay" role="status">
                <strong>Recorded run, not live.</strong> The live run failed ({state.cachedReplay.reason}), so this
                is a saved run of the same example on the same {state.personaCount} people.
              </p>
            )}
            {!state.retrying && state.status === STATUS.CANCELLED && (
              <p className="run-note">
                Run cancelled.
                {doneCount > 0 && ` The ${doneCount} reactions already in are kept below.`}
              </p>
            )}
            {!state.retrying && state.status === STATUS.ERROR && state.error && (
              <p className="run-note run-note-error" role="alert">
                {state.error}
              </p>
            )}
            {marketBusy && !isRunning && (
              <p className="run-note">The market comparison is still running. Stop it to run again now.</p>
            )}
            {confirmCheck && !isRunning && (
              <div className="check-confirm" role="alert">
                <span className="check-confirm-kind">
                  This looks like {confirmCheck.kind}
                </span>
                <span className="check-confirm-reason">{confirmCheck.reason}</span>
                <span className="check-confirm-actions">
                  <button
                    type="button"
                    className="check-confirm-go"
                    onClick={() => {
                      setConfirmCheckEntry(null)
                      startRunNow()
                    }}
                  >
                    Run anyway
                  </button>
                  <button
                    type="button"
                    className="check-confirm-cancel"
                    onClick={() => setConfirmCheckEntry(null)}
                  >
                    Let me edit it
                  </button>
                </span>
              </div>
            )}
          </div>
        </aside>
        {confirmCancel && (
        <div
          className="modal-backdrop"
          onClick={() => setConfirmCancel(false)}
          role="presentation"
        >
          <div
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="cancel-modal-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="modal-title" id="cancel-modal-title">
              Cancel this run?
            </h2>
            <p className="modal-body">
              {!isRunning
                ? "The compare run stops here. Your first run's results are kept, but there will be no comparison."
                : doneCount > 0
                ? `${doneCount} of ${total} people have already read it. Those reactions are kept, but the run stops here and there will be no verdict.`
                : 'The run stops here and there will be no verdict.'}
            </p>
            <div className="modal-actions">
              <button
                type="button"
                className="modal-cancel"
                onClick={() => setConfirmCancel(false)}
              >
                Keep running
              </button>
              <button
                type="button"
                className="modal-destructive"
                onClick={handleCancelConfirmed}
                autoFocus
              >
                Cancel the run
              </button>
            </div>
          </div>
        </div>
      )}
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
          onRemoveSegment={handleRemoveSegment}
          onClearAll={handleClearFilters}
          matchCount={filteredIds ? filteredIds.size : state.personas.length}
          totalCount={state.personas.length}
        />
      </div>
      <div className="app-column app-column-right">
        <Verdict
          synthesis={state.synthesis}
          runLabel={state.label || 'Untitled run'}
          wouldActPct={statsA.wouldActPct}
          total={statsA.usable}
          onApplyAndCompare={handleApplyAndCompare}
          // Re-enabled after a compare run fails or is cancelled, so it can be retried.
          applyDisabled={state.compare.visible && state.compare.status !== STATUS.ERROR}
          applyError={applyError ?? state.compare.error}
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

          <StatsStrip
            liveStats={liveStats}
            runStats={runStats}
            elapsedSeconds={elapsedSeconds}
            status={statsStripStatus}
            answered={answeredA}
          />

          <Grid
            personas={displayPersonas}
            reactionsById={gridReactionsById}
            filteredIds={filteredIds}
            highlightedIds={ringedIds}
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
            reactionsAById={state.reactionsById}
          />
        )}

        <Findings
          synthesis={state.synthesis}
          onSegmentClick={handleSegmentClick}
          onEvidenceHoverStart={handleEvidenceHoverStart}
          onEvidenceHoverEnd={handleEvidenceHoverEnd}
        />

        {/* Below the findings: the market comparison needs a finished run
            to compare, and reads as "and here is how that sits against
            everyone else" rather than competing with the verdict. Mock
            mode gets a synthetic version -- nothing is searched or read,
            and the card says so. */}
        {features.market && state.synthesis && (state.runId || isMockMode()) && (
          <MarketPanel
            status={market?.status}
            query={market?.query ?? ''}
            pages={market?.pages ?? []}
            pagesDone={market?.pagesDone ?? []}
            comparison={market?.comparison ?? null}
            error={market?.error ?? null}
            onStart={handleStartBenchmark}
            onDismiss={handleDismissMarket}
            // A compare run holds the same per-client slot a run does, so
            // the server would refuse this while one is in flight.
            disabled={isRunning || compareIsRunning}
            isMock={isMockMode()}
          />
        )}
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
