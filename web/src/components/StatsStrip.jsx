// Live stats: reactions in, mean sentiment, would-act %, elapsed. Section 6.4.

import { STATUS } from '../state.js'

const ULTRA_MODEL_LABEL = 'Nemotron Ultra'

export default function StatsStrip({ liveStats, elapsedSeconds, status }) {
  const { done, total, meanSentiment, wouldActPct } = liveStats

  return (
    <div className="stats-strip">
      <div className="stat">
        <span className="stat-value">{done}/{total}</span>
        <span className="stat-label">reactions in</span>
      </div>
      <div className="stat">
        <span className="stat-value">{meanSentiment.toFixed(2)}</span>
        <span className="stat-label">mean sentiment</span>
      </div>
      <div className="stat">
        <span className="stat-value">{Math.round(wouldActPct)}%</span>
        <span className="stat-label">would act</span>
      </div>
      <div className="stat">
        <span className="stat-value">{elapsedSeconds.toFixed(1)}s</span>
        <span className="stat-label">elapsed</span>
      </div>
      {status === STATUS.SYNTHESIZING && (
        <div className="synthesizing-line">
          Reading all {total} reactions with {ULTRA_MODEL_LABEL}...
        </div>
      )}
    </div>
  )
}
