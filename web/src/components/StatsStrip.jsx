// Live stats: reactions in, mean sentiment, would-act %, elapsed. Once a
// run is done, also the real number of calls made to each Nemotron tier,
// retries included -- the model tiering, counted rather than described.

import { STATUS } from '../state.js'

// Matches panel/synthesize.py's SHARD_THRESHOLD: above it, Super condenses
// the reactions into themes and Ultra reads those, not every reaction.
const SHARD_THRESHOLD = 150

// Over usable reactions, as the backend counts them: failed calls are
// never sent to synthesis, so they neither trigger sharding nor get read.
function synthesizingLine(answered) {
  if (answered > SHARD_THRESHOLD) {
    return `Nemotron Super is condensing ${answered} reactions into themes for Nemotron Ultra...`
  }
  return `Nemotron Ultra is reading all ${answered} reactions...`
}

function modelCallsLine(runStats) {
  if (!runStats) return null
  const parts = [
    [runStats.nano_calls, 'Nano'],
    [runStats.super_calls, 'Super'],
    [runStats.ultra_calls, 'Ultra'],
  ]
    .filter(([n]) => n)
    .map(([n, name]) => `${n.toLocaleString()} ${name}`)
  return parts.length ? `Nemotron calls: ${parts.join(' · ')}` : null
}

// answered is the whole run's usable reactions, not the filtered count: the
// synthesis covers the whole panel whatever filter is on screen.
export default function StatsStrip({ liveStats, runStats, elapsedSeconds, status, answered }) {
  const { done, total, failed, meanSentiment, wouldActPct } = liveStats
  const callsLine = status === STATUS.DONE ? modelCallsLine(runStats) : null

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
      {(callsLine || failed > 0) && (
        <div className="stats-footnote">
          {callsLine}
          {callsLine && failed > 0 && ' · '}
          {failed > 0 && `${failed} ${failed === 1 ? 'reaction' : 'reactions'} failed and ${failed === 1 ? 'is' : 'are'} left out of the averages`}
        </div>
      )}
      {status === STATUS.SYNTHESIZING && (
        <div className="synthesizing-line">{synthesizingLine(answered)}</div>
      )}
    </div>
  )
}
