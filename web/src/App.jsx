import { useEffect, useReducer, useRef } from 'react'
import { getPoolSize, previewPersonas, startRun } from './api.js'
import ArtifactInput from './components/ArtifactInput.jsx'
import Grid from './components/Grid.jsx'
import PersonaCountControl from './components/PersonaCountControl.jsx'
import StatsStrip from './components/StatsStrip.jsx'
import { initialState, reducer, selectElapsedSeconds, selectIsRunning, selectLiveStats } from './state.js'

const DEFAULT_PERSONA_COUNT = 100
const TICK_INTERVAL_MS = 250

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState)
  const runHandleRef = useRef(null)

  // Load the pool size once, then set the initial N and its preview
  // personas (grid shows empty tiles for N before any run, section 6.10).
  useEffect(() => {
    getPoolSize().then((poolSize) => {
      dispatch({ type: 'SET_POOL_SIZE', poolSize })
      const n = Math.min(DEFAULT_PERSONA_COUNT, poolSize)
      dispatch({ type: 'SET_PERSONA_COUNT', n, personas: previewPersonas(n) })
    })
  }, [])

  const isRunning = selectIsRunning(state)

  // Elapsed-seconds ticker, section 6.4 ("elapsed stops" on done).
  useEffect(() => {
    if (!isRunning) return
    const id = setInterval(() => dispatch({ type: 'TICK' }), TICK_INTERVAL_MS)
    return () => clearInterval(id)
  }, [isRunning])

  useEffect(() => {
    return () => runHandleRef.current?.cancel()
  }, [])

  function handlePersonaCountChange(n) {
    if (isRunning) return
    runHandleRef.current?.cancel()
    dispatch({ type: 'SET_PERSONA_COUNT', n, personas: previewPersonas(n) })
  }

  function handleRun() {
    runHandleRef.current?.cancel()
    dispatch({ type: 'RUN_START' })
    runHandleRef.current = startRun({
      personaCount: state.personaCount,
      onEvent: (event) => {
        switch (event.type) {
          case 'reaction':
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
          case 'error':
            dispatch({ type: 'ERROR', message: event.data?.message ?? 'unknown error' })
            break
          default:
            break
        }
      },
    })
  }

  const liveStats = selectLiveStats(state)
  const elapsedSeconds = selectElapsedSeconds(state)

  return (
    <div className="app-shell">
      <div className="app-column app-column-left">
        <h1 className="app-title">Panel</h1>
        <ArtifactInput
          mode={state.inputMode}
          text={state.artifactText}
          url={state.artifactUrl}
          label={state.label}
          isRunning={isRunning}
          onModeChange={(mode) => dispatch({ type: 'SET_INPUT_MODE', mode })}
          onTextChange={(text) => dispatch({ type: 'SET_ARTIFACT_TEXT', text })}
          onUrlChange={(url) => dispatch({ type: 'SET_ARTIFACT_URL', url })}
          onLabelChange={(label) => dispatch({ type: 'SET_LABEL', label })}
          onRun={handleRun}
        />
        <PersonaCountControl
          n={state.personaCount}
          poolSize={state.poolSize}
          onChange={handlePersonaCountChange}
          disabled={isRunning}
        />
      </div>
      <div className="app-column app-column-right">
        <Grid personas={state.personas} reactionsById={state.reactionsById} />
        <StatsStrip liveStats={liveStats} elapsedSeconds={elapsedSeconds} status={state.status} />
      </div>
    </div>
  )
}
