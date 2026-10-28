// N slider -- step 2 ("Who reads it") of the setup card. The count is also
// an editable number inline in the step header, for precise entry.

// Throughput, not per-call latency, at MAX_CONCURRENCY=50. 100 reactions
// once took ~35-40s; measured 2026-10-28 Nano's median call was 13-21s,
// so a 100-persona read now takes about 70-140s (README, Performance).
const SECONDS_PER_PERSONA_ESTIMATE = 0.8
// Fast mode (reasoning off): measured median calls of 3-7s against
// 13-21s, so a 100-persona read lands under 20 seconds.
const FAST_SECONDS_PER_PERSONA_ESTIMATE = 0.15

function snapToStep(n, step, min, max) {
  const snapped = Math.round(n / step) * step
  return Math.min(max, Math.max(min, snapped))
}

export default function PersonaCountControl({ n, poolSize, onChange, disabled = false, fast = false }) {
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
        {Math.round(n * (fast ? FAST_SECONDS_PER_PERSONA_ESTIMATE : SECONDS_PER_PERSONA_ESTIMATE))} seconds.
      </span>
    </div>
  )
}
