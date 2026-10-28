// Segment filter chips, section 6.5. "Clear all" and the match-count line
// are new (the mockup shows both prominently) -- cheap additions wired to
// data App.jsx already has (activeFilters, filteredIds, personas.length),
// not a new feature, just exposing what already exists.
//
// Region is a multi-select dropdown, age and income are dual-handle range
// sliders (DualRangeSlider.jsx) -- changed by request on 2026-10-29. Tech
// comfort and skepticism stay chips; they weren't part of that request.

import { useEffect, useRef, useState } from 'react'
import { REGIONS, INCOME_BANDS } from '../mock.js'
import DualRangeSlider from './DualRangeSlider.jsx'

const SCORE_BANDS = ['low', 'mid', 'high']
const SCORE_GROUPS = [
  { group: 'techComfort', label: 'TECH COMFORT', values: SCORE_BANDS },
  { group: 'skepticism', label: 'SKEPTICISM', values: SCORE_BANDS },
]

// Fixed, generous bound -- not derived from whatever ages happen to be in
// the current pool, so the slider's range doesn't shift under you as N
// changes.
export const AGE_MIN = 18
export const AGE_MAX = 85

function isActive(activeFilters, group, value) {
  return activeFilters.some((f) => f.group === group && f.value === value)
}

function rangeFor(activeFilters, group, defaultMin, defaultMax, minKey = 'min', maxKey = 'max') {
  const f = activeFilters.find((x) => x.group === group)
  return f ? [f[minKey], f[maxKey]] : [defaultMin, defaultMax]
}

function RegionDropdown({ options, selected, onToggleValue }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function onKeyDown(e) {
      if (e.key === 'Escape') setOpen(false)
    }
    function onClickOutside(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    window.addEventListener('keydown', onKeyDown)
    window.addEventListener('mousedown', onClickOutside)
    return () => {
      window.removeEventListener('keydown', onKeyDown)
      window.removeEventListener('mousedown', onClickOutside)
    }
  }, [open])

  const label = selected.length === 0 ? 'Any region' : selected.length === 1 ? selected[0] : `${selected.length} regions`

  return (
    <div className="filter-dropdown" ref={ref}>
      <button
        type="button"
        className={`filter-dropdown-trigger ${selected.length ? 'active' : ''}`}
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-haspopup="listbox"
      >
        <span>{label}</span>
        <span className="filter-dropdown-caret" aria-hidden="true">
          &#9662;
        </span>
      </button>
      {open && (
        <div className="filter-dropdown-panel" role="group" aria-label="Region">
          {options.map((opt) => (
            <label key={opt} className="filter-dropdown-option">
              <input type="checkbox" checked={selected.includes(opt)} onChange={() => onToggleValue(opt)} />
              <span>{opt}</span>
            </label>
          ))}
        </div>
      )}
    </div>
  )
}

export default function Filters({ activeFilters, onToggle, onRangeChange, onClearAll, matchCount, totalCount }) {
  const selectedRegions = activeFilters.filter((f) => f.group === 'region').map((f) => f.value)
  const [ageMin, ageMax] = rangeFor(activeFilters, 'ageBand', AGE_MIN, AGE_MAX)
  const [incomeMinIdx, incomeMaxIdx] = rangeFor(
    activeFilters,
    'incomeBand',
    0,
    INCOME_BANDS.length - 1,
    'minIndex',
    'maxIndex',
  )

  return (
    <div className="filters">
      <div className="filters-top">
        <span className="filters-title">Filter the panel</span>
        {activeFilters.length > 0 && (
          <button type="button" className="filters-clear" onClick={onClearAll}>
            Clear all
          </button>
        )}
      </div>
      <span className="filters-match">
        {activeFilters.length > 0 ? `${matchCount} of ${totalCount} people match` : `Showing all ${totalCount} people`}
      </span>

      <div className="filter-group">
        <span className="filter-group-label">REGION</span>
        <RegionDropdown
          options={REGIONS}
          selected={selectedRegions}
          onToggleValue={(value) => onToggle({ group: 'region', value })}
        />
      </div>

      <div className="filter-group">
        <span className="filter-group-label">AGE</span>
        <DualRangeSlider
          min={AGE_MIN}
          max={AGE_MAX}
          value={[ageMin, ageMax]}
          onChange={([min, max]) =>
            onRangeChange('ageBand', { min, max }, min === AGE_MIN && max === AGE_MAX)
          }
        />
      </div>

      <div className="filter-group">
        <span className="filter-group-label">INCOME</span>
        <DualRangeSlider
          min={0}
          max={INCOME_BANDS.length - 1}
          value={[incomeMinIdx, incomeMaxIdx]}
          formatLabel={(idx) => INCOME_BANDS[idx]}
          onChange={([minIndex, maxIndex]) =>
            onRangeChange(
              'incomeBand',
              { minIndex, maxIndex },
              minIndex === 0 && maxIndex === INCOME_BANDS.length - 1,
            )
          }
        />
      </div>

      {SCORE_GROUPS.map(({ group, label, values }) => (
        <div className="filter-group" key={group}>
          <span className="filter-group-label">{label}</span>
          <div className="filter-chips">
            {values.map((value) => (
              <button
                key={value}
                type="button"
                className={`filter-chip ${isActive(activeFilters, group, value) ? 'active' : ''}`}
                onClick={() => onToggle({ group, value })}
              >
                {value}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  )
}
