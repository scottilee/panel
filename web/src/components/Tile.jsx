// React.memo'd single tile, keyed by persona_id, section 6.2-6.3.

import { memo } from 'react'

const SENTIMENT_CLASS = {
  '-2': 'tile-sentiment-neg2',
  '-1': 'tile-sentiment-neg1',
  0: 'tile-sentiment-0',
  1: 'tile-sentiment-pos1',
  2: 'tile-sentiment-pos2',
}

function Tooltip({ persona, reaction }) {
  return (
    <div className="tile-tooltip" role="tooltip">
      <div className="tile-tooltip-name">{persona.name}</div>
      <div className="tile-tooltip-meta">
        {persona.age} &middot; {persona.occupation} &middot; {persona.location}
      </div>
      {reaction && !reaction.raw_error && (
        <div className="tile-tooltip-gut">&ldquo;{reaction.gut_reaction}&rdquo;</div>
      )}
    </div>
  )
}

function Tile({ persona, reaction, dimmed = false, onClick }) {
  const className = [
    'tile',
    !reaction ? 'tile-empty' : reaction.raw_error ? 'tile-error' : 'tile-filled',
    reaction && !reaction.raw_error ? SENTIMENT_CLASS[reaction.sentiment] ?? 'tile-sentiment-0' : '',
    dimmed ? 'tile-dimmed' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={className}
      onClick={(e) => {
        e.stopPropagation()
        onClick?.(persona)
      }}
    >
      {reaction && !reaction.raw_error && reaction.would_act && <span className="tile-dot" />}
      <Tooltip persona={persona} reaction={reaction} />
    </div>
  )
}

function propsAreEqual(prev, next) {
  return (
    prev.persona.id === next.persona.id &&
    prev.reaction === next.reaction &&
    prev.dimmed === next.dimmed &&
    prev.onClick === next.onClick
  )
}

export default memo(Tile, propsAreEqual)
