// Compare view toggle (Run A / Run B / Delta) + before/after stats + the
// one-sentence Ultra summary.
//
// Every number here is over the same people: the personas with a usable
// reaction in BOTH runs (CompareResult.compared, listed in `deltas`). The
// flips are counted over exactly that set, so the before/after averages
// are too, and the would-act change is the server's own figure.

const VIEWS = [
  { id: 'A', label: 'Run A' },
  { id: 'B', label: 'Run B' },
  { id: 'delta', label: 'Delta' },
]

// Rerunning the identical artifact against the identical 100-person panel
// moved 7 people on one run and 4 on an immediate rerun. That was measured
// at temperature 0.7; reactions now run at 0.4, whose noise hasn't been
// measured, so this is an upper-end reference, not a confidence interval.
const RERUN_NOISE_PER_100 = [4, 7]

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

function noiseLine(compared) {
  const [lo, hi] = RERUN_NOISE_PER_100
  const range =
    compared === 100
      ? `${lo}–${hi} people per 100`
      : `about ${Math.round((lo * compared) / 100)}–${Math.round((hi * compared) / 100)} of ${compared}`
  return `For scale: an unchanged rerun has moved ${range} (measured at an earlier, noisier setting).`
}

export default function CompareBar({ compareResult, viewMode, onSetViewMode, reactionsAById }) {
  if (!compareResult) return null
  const { deltas, compared } = compareResult
  const n = deltas.length || 1
  const meanBefore = deltas.reduce((sum, d) => sum + d.sentiment_before, 0) / n
  const meanAfter = deltas.reduce((sum, d) => sum + d.sentiment_after, 0) / n
  const actBefore = (100 * deltas.filter((d) => reactionsAById[d.persona_id]?.would_act).length) / n
  const actAfter = actBefore + compareResult.would_act_delta_pct

  return (
    <div className="compare-bar">
      <p className="compare-summary">{compareResult.summary}</p>

      <div className="compare-stats">
        <StatPair label="mean sentiment" before={meanBefore.toFixed(2)} after={meanAfter.toFixed(2)} />
        <StatPair
          label="would act"
          before={Math.round(actBefore)}
          after={Math.round(actAfter)}
          suffix="%"
        />
        <div className="compare-stat">
          <span className="compare-stat-label">flipped</span>
          <span className="compare-stat-value">
            +{compareResult.flipped_positive} / -{compareResult.flipped_negative}
          </span>
        </div>
      </div>
      <p className="compare-note">
        Over the {compared} people who answered both times. {noiseLine(compared)}
      </p>

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
