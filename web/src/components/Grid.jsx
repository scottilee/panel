// Tile grid, memoized Tile, section 6.2. filteredIds (section 6.5) dims
// tiles outside the active segment; onBackgroundClick (section 6.3) fires
// when the click didn't land on a tile (Tile.jsx stops propagation on its
// own click, so this only sees clicks on the grid's own background).

import { gridLayoutFor } from '../state.js'
import Tile from './Tile.jsx'

export default function Grid({ personas, reactionsById, filteredIds = null, onTileClick, onBackgroundClick }) {
  const { cols, tile } = gridLayoutFor(personas.length)

  return (
    <div
      className="grid"
      style={{ '--cols': cols, '--tile': `${tile}px` }}
      onClick={onBackgroundClick}
    >
      {personas.map((persona) => (
        <Tile
          key={persona.id}
          persona={persona}
          reaction={reactionsById[persona.id]}
          dimmed={filteredIds !== null && !filteredIds.has(persona.id)}
          onClick={onTileClick}
        />
      ))}
    </div>
  )
}
