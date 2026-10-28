// N slider -- step 2 ("Who reads it") of the setup card, section 6.10.
// The count is shown as an editable number inline in the step header
// (matching the mockup's big bold readout) rather than a separate number
// spinner below the slider, so the precise-entry feature the original
// control had isn't lost, just relocated -- flagged as a judgment call.

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
