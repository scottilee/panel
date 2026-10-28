// React.memo'd single tile, keyed by persona_id.

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

// deltaKind: 'positive' | 'negative' | 'unchanged' | undefined.
// When set, it overrides the normal sentiment coloring -- positive/negative
// become solid green/red (letting .tile's always-on background-color
// transition animate the change), unchanged keeps its own sentiment color
// but fades to 50% opacity. transitionDelayMs staggers that transition
// per tile so switching into delta view reads as a wave, not a blink.
const DELTA_CLASS = {
  positive: 'tile-delta-positive',
  negative: 'tile-delta-negative',
}

function Tile({ persona, reaction, dimmed = false, highlighted = false, deltaKind, transitionDelayMs = 0, onClick }) {
  const className = [
    'tile',
    !reaction ? 'tile-empty' : reaction.raw_error ? 'tile-error' : 'tile-filled',
    reaction && !reaction.raw_error ? SENTIMENT_CLASS[reaction.sentiment] ?? 'tile-sentiment-0' : '',
    deltaKind ? DELTA_CLASS[deltaKind] ?? '' : '',
    deltaKind === 'unchanged' ? 'tile-delta-unchanged' : '',
    dimmed ? 'tile-dimmed' : '',
    highlighted ? 'tile-highlighted' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div
      className={className}
      style={deltaKind ? { transitionDelay: `${transitionDelayMs}ms` } : undefined}
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
    prev.highlighted === next.highlighted &&
    prev.deltaKind === next.deltaKind &&
    prev.transitionDelayMs === next.transitionDelayMs &&
    prev.onClick === next.onClick
  )
}

export default memo(Tile, propsAreEqual)
