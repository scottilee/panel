// Compare view toggle (Run A / Run B / Delta) + before/after stats + the
// one-sentence Ultra summary, section 6.7.

const VIEWS = [
  { id: 'A', label: 'Run A' },
  { id: 'B', label: 'Run B' },
  { id: 'delta', label: 'Delta' },
]

function StatPair({ label, before, after, suffix = '' }) {
  return (
    <div className="compare-stat">
      <span className="compare-stat-label">{label}</span>
      <span className="compare-stat-value">
        {before}
        {suffix} &rarr; {after}
        {suffix}
      </span>
    </div>
  )
}

export default function CompareBar({ compareResult, viewMode, onSetViewMode, statsA, statsB }) {
  if (!compareResult) return null

  return (
    <div className="compare-bar">
      <p className="compare-summary">{compareResult.summary}</p>

      <div className="compare-stats">
        <StatPair label="mean sentiment" before={statsA.meanSentiment.toFixed(2)} after={statsB.meanSentiment.toFixed(2)} />
        <StatPair
          label="would act"
          before={Math.round(statsA.wouldActPct)}
          after={Math.round(statsB.wouldActPct)}
          suffix="%"
        />
        <div className="compare-stat">
          <span className="compare-stat-label">flipped</span>
          <span className="compare-stat-value">
            +{compareResult.flipped_positive} / -{compareResult.flipped_negative}
          </span>
        </div>
      </div>

      <div className="compare-view-toggle">
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            className={viewMode === v.id ? 'active' : ''}
            onClick={() => onSetViewMode(v.id)}
          >
            {v.label}
          </button>
        ))}
      </div>
    </div>
  )
}
