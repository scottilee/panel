// Reusable dual-handle range slider: two overlapping native
// <input type="range"> elements (standard, robust pattern) with a custom
// track/fill drawn underneath. Each input is made fully transparent
// except its thumb (via ::-webkit-slider-thumb/::-moz-range-thumb), so
// both handles stay independently draggable and keyboard-operable
// (arrow keys) rather than one trapping all pointer events.
export default function DualRangeSlider({ min, max, step = 1, value, onChange, formatLabel }) {
  const [lo, hi] = value
  const fmt = formatLabel ?? ((v) => v)

  function handleLoChange(e) {
    const next = Math.min(Number(e.target.value), hi)
    onChange([next, hi])
  }

  function handleHiChange(e) {
    const next = Math.max(Number(e.target.value), lo)
    onChange([lo, next])
  }

  const loPct = ((lo - min) / (max - min)) * 100
  const hiPct = ((hi - min) / (max - min)) * 100

  return (
    <div className="dual-range">
      <div className="dual-range-track-wrap">
        <div className="dual-range-track" />
        <div className="dual-range-fill" style={{ left: `${loPct}%`, right: `${100 - hiPct}%` }} />
        <input
          type="range"
          className="dual-range-input"
          min={min}
          max={max}
          step={step}
          value={lo}
          onChange={handleLoChange}
          aria-label="Minimum"
        />
        <input
          type="range"
          className="dual-range-input"
          min={min}
          max={max}
          step={step}
          value={hi}
          onChange={handleHiChange}
          aria-label="Maximum"
        />
      </div>
      <div className="dual-range-labels">
        <span>{fmt(lo)}</span>
        <span>{fmt(hi)}</span>
      </div>
    </div>
  )
}
