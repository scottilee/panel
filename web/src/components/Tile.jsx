// React.memo'd single tile, keyed by persona_id, section 6.2-6.3. Stage 1-2, Agent B.
//
// Hover tooltip and click-to-pin (section 6.3) land in stage 2; this stage
// only renders the fill/color/dot/error states and the 150ms scale-in.

import { memo } from 'react'

const SENTIMENT_CLASS = {
  '-2': 'tile-sentiment-neg2',
  '-1': 'tile-sentiment-neg1',
  0: 'tile-sentiment-0',
  1: 'tile-sentiment-pos1',
  2: 'tile-sentiment-pos2',
}

function Tile({ persona, reaction }) {
  if (!reaction) {
    return <div className="tile tile-empty" title={persona.name} />
  }

  if (reaction.raw_error) {
    return <div className="tile tile-error" title={`${persona.name} — no response`} />
  }

  const sentimentClass = SENTIMENT_CLASS[reaction.sentiment] ?? 'tile-sentiment-0'

  return (
    <div className={`tile tile-filled ${sentimentClass}`} title={persona.name}>
      {reaction.would_act && <span className="tile-dot" />}
    </div>
  )
}

function propsAreEqual(prev, next) {
  return prev.persona.id === next.persona.id && prev.reaction === next.reaction
}

export default memo(Tile, propsAreEqual)
