// N slider -- step 2 ("Who reads it") of the setup card. The count is also
// an editable number inline in the step header, for precise entry.

// Throughput, not per-call latency: measured at 100 reactions in ~35-40s
// at MAX_CONCURRENCY=50.
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
    <div className="setup-step">
      <div className="step-header">
        <span className="step-num">2</span>
        <label htmlFor="persona-count-slider" className="step-label">
          Who reads it
        </label>
        <input
          id="persona-count-number"
          type="number"
          className="step-count-input"
          min={min}
          max={poolSize}
          step={step}
          value={n}
          disabled={disabled}
          onChange={(e) => handleChange(e.target.value)}
        />
      </div>
      <input
        id="persona-count-slider"
        type="range"
        className="count-range"
        min={min}
        max={poolSize}
        step={step}
        value={n}
        disabled={disabled}
        onChange={(e) => handleChange(e.target.value)}
      />
      <span className="count-caption">
        Balanced across region, age, income and personality. About {n} Nano calls, roughly{' '}
        {Math.round(n * SECONDS_PER_PERSONA_ESTIMATE)} seconds.
      </span>
    </div>
  )
}
