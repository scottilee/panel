// Segment filter chips, section 6.5.

import { REGIONS, INCOME_BANDS } from '../mock.js'

const AGE_BANDS = ['18-24', '25-34', '35-49', '50-64', '65+']
const SCORE_BANDS = ['low', 'mid', 'high']

const GROUPS = [
  { group: 'region', label: 'Region', values: REGIONS },
  { group: 'ageBand', label: 'Age', values: AGE_BANDS },
  { group: 'incomeBand', label: 'Income', values: INCOME_BANDS },
  { group: 'techComfort', label: 'Tech comfort', values: SCORE_BANDS },
  { group: 'skepticism', label: 'Skepticism', values: SCORE_BANDS },
]

function isActive(activeFilters, group, value) {
  return activeFilters.some((f) => f.group === group && f.value === value)
}

export default function Filters({ activeFilters, onToggle }) {
  return (
    <div className="filters">
      {GROUPS.map(({ group, label, values }) => (
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
