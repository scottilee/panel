// N slider + number field, section 6.10. Stage 1, Agent B.

// Empirically calibrated from section 5.2's hour-one measurement target
// (100 reactions under ~40s at the measured MAX_CONCURRENCY) -- a
// throughput constant, not a per-call serial latency.
const SECONDS_PER_PERSONA_ESTIMATE = 0.4

function snapToStep(n, step, min, max) {
  const snapped = Math.round(n / step) * step
  return Math.min(max, Math.max(min, snapped))
}

export default function PersonaCountControl({ n, poolSize, onChange, disabled = false }) {
  const min = 10
  const step = 10

  function handleChange(raw) {
    const parsed = Number(raw)
    if (Number.isNaN(parsed)) return
    onChange(snapToStep(parsed, step, min, poolSize))
  }

  return (
    <div className="persona-count-control">
      <label htmlFor="persona-count-slider">{n} people</label>
      <div className="persona-count-inputs">
        <input
          id="persona-count-slider"
          type="range"
          min={min}
          max={poolSize}
          step={step}
          value={n}
          disabled={disabled}
          onChange={(e) => handleChange(e.target.value)}
        />
        <input
          type="number"
          min={min}
          max={poolSize}
          step={step}
          value={n}
          disabled={disabled}
          onChange={(e) => handleChange(e.target.value)}
        />
      </div>
      <div className="persona-count-estimate">
        about {n} Nano calls, roughly {Math.round(n * SECONDS_PER_PERSONA_ESTIMATE)} seconds
      </div>
    </div>
  )
}
