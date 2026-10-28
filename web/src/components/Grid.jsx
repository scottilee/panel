// Tile grid, memoized Tile, section 6.2. filteredIds (section 6.5) dims
// tiles outside the active segment; onBackgroundClick (section 6.3) fires
// when the click didn't land on a tile (Tile.jsx stops propagation on its
// own click, so this only sees clicks on the grid's own background).
// highlightedIds (section 6.6) rings tiles while hovering a finding's
// evidence avatars -- distinct from filter dimming, both can apply at once.
// deltaByPersonaId (section 6.7), when present, switches every tile to
// delta coloring and staggers each one's color transition by 20ms *
// array index so it reads as a wave.

import { gridLayoutFor } from '../state.js'
import Tile from './Tile.jsx'

const WAVE_STAGGER_MS = 20

export default function Grid({
  personas,
  reactionsById,
  filteredIds = null,
  highlightedIds = null,
  deltaByPersonaId = null,
  onTileClick,
  onBackgroundClick,
}) {
  const { cols, tile } = gridLayoutFor(personas.length)

  return (
    <div
      className="grid"
      style={{ '--cols': cols, '--tile': `${tile}px` }}
      onClick={onBackgroundClick}
    >
      {personas.map((persona, index) => (
        <Tile
          key={persona.id}
          persona={persona}
          reaction={reactionsById[persona.id]}
          dimmed={filteredIds !== null && !filteredIds.has(persona.id)}
          highlighted={highlightedIds !== null && highlightedIds.has(persona.id)}
          deltaKind={deltaByPersonaId ? deltaByPersonaId[persona.id] : undefined}
          transitionDelayMs={index * WAVE_STAGGER_MS}
          onClick={onTileClick}
        />
      ))}
    </div>
  )
}
