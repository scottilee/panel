// Tile grid, memoized Tile, section 6.2. Stage 1, Agent B.

import { gridLayoutFor } from '../state.js'
import Tile from './Tile.jsx'

export default function Grid({ personas, reactionsById }) {
  const { cols, tile } = gridLayoutFor(personas.length)

  return (
    <div
      className="grid"
      style={{ '--cols': cols, '--tile': `${tile}px` }}
    >
      {personas.map((persona) => (
        <Tile key={persona.id} persona={persona} reaction={reactionsById[persona.id]} />
      ))}
    </div>
  )
}
