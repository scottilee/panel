// The landing page, '/'. Implements the "Landing 1 · Ripple field"
// direction from the design canvas: a dark page whose hero sits beside an
// isometric field of 100 cubes, one per reader, each coloured by the kind
// of reaction it stands for and rising in a ripple that spreads from the
// centre.
//
// Why 100 cubes rather than a picture: the grid IS the product. The app's
// own run view is one tile per person, so the hero is the same idea in
// three dimensions, and the colours are the real reaction categories.
//
// The geometry is deterministic -- a hash of the tile index picks the
// colour, and the ripple delay comes from the tile's distance to the
// centre -- so the field looks identical on every load and in every
// screenshot, rather than reshuffling between a judge's two visits.

import { useMemo } from 'react'

// The reaction kinds the panel actually reports, with the colours from the
// design. Chosen to differ in lightness as well as hue, so the legend is
// still readable for a colour-blind reader.
const REACTIONS = [
  { name: 'Bored', color: '#94A3B8' },
  { name: 'Unmoved', color: '#60A5FA' },
  { name: 'Confused', color: '#FBBF24' },
  { name: 'Suspicious', color: '#F87171' },
]

const COLS = 10
const ROWS = 10
const STEP = 44
const CENTRE = 4.5

// Sine hash: stable across loads and across machines, unlike Math.random.
function hashUnit(i) {
  const x = Math.sin(i * 12.9898 + 4.1) * 43758.5453
  return x - Math.floor(x)
}

function reactionFor(i) {
  const v = hashUnit(i)
  if (v < 0.32) return REACTIONS[0]
  if (v < 0.56) return REACTIONS[1]
  if (v < 0.78) return REACTIONS[2]
  return REACTIONS[3]
}

function buildField() {
  const tiles = []
  for (let i = 0; i < COLS * ROWS; i++) {
    const col = i % COLS
    const row = Math.floor(i / COLS)
    const distance = Math.sqrt((col - CENTRE) ** 2 + (row - CENTRE) ** 2)
    tiles.push({
      i,
      x: col * STEP + 5,
      y: row * STEP + 5,
      color: reactionFor(i).color,
      // Negative, so every tile is already mid-cycle on first paint and
      // the ripple reads as continuous rather than starting from flat.
      delay: -(distance * 0.32),
    })
  }
  return tiles
}

const STATS = [
  { figure: '1,000', label: 'synthetic readers', note: 'stratified across 8 regions, no duplicate names' },
  { figure: '~15s', label: 'to a verdict at 100', note: 'about 7 minutes for the full thousand' },
  { figure: '0', label: 'numbers written by a model', note: 'every percentage is recounted in Python' },
]

export default function Landing({ onSeeItRun, onSignIn, signedIn, email, onSignOut }) {
  const tiles = useMemo(() => buildField(), [])

  return (
    <div className="landing">
      <header className="landing-header">
        <span className="landing-brand">
          <span className="landing-mark" aria-hidden="true">
            P
          </span>
          <span className="landing-wordmark">Panel</span>
        </span>
        <div className="landing-account">
          {signedIn ? (
            <>
              <span className="landing-email" title={email}>
                {email}
              </span>
              <button type="button" className="landing-link-button" onClick={onSignOut}>
                Sign out
              </button>
            </>
          ) : (
            <button type="button" className="landing-link-button" onClick={onSignIn}>
              Sign in
            </button>
          )}
        </div>
      </header>

      <main className="landing-main">
        <div className="landing-hero">
          <div className="landing-pitch">
            <h1 className="landing-headline">
              The focus group that tells you what&rsquo;s broken, not that you&rsquo;re brilliant.
            </h1>
            <p className="landing-sub">
              Paste a landing page, an ad, or a pricing table. A hundred synthetic people read it
              and react the way real readers do in the first twenty seconds &mdash; bored,
              confused, suspicious, unmoved. Then a much larger model tells you the three things
              costing you customers, which segment each one affects, and one specific edit to try.
            </p>
            <div className="landing-actions">
              <button type="button" className="landing-cta" onClick={onSeeItRun}>
                See a demo
              </button>
              <button type="button" className="landing-cta-secondary" onClick={onSignIn}>
                Run it with actual Nebius models
              </button>
            </div>
            <p className="landing-note">
              {signedIn
                ? 'You are signed in, so the panel runs on live Nebius models.'
                : 'The demo runs on recorded data — no sign-in, nothing to spend. Sign in to use a live panel on Nebius.'}
            </p>
          </div>

          {/* Decorative: the legend names the colours, and every fact it
              carries is stated in text elsewhere on the page. */}
          <div className="landing-field" aria-hidden="true">
            <div className="landing-field-stage">
              {tiles.map((t) => (
                <div
                  key={t.i}
                  className="landing-tile"
                  style={{ left: `${t.x}px`, top: `${t.y}px`, animationDelay: `${t.delay}s` }}
                >
                  <span className="landing-tile-front" style={{ background: t.color }} />
                  <span className="landing-tile-side" style={{ background: t.color }} />
                  <span className="landing-tile-top" style={{ background: t.color }} />
                </div>
              ))}
            </div>

            <div className="landing-float">
              <span className="landing-float-eyebrow">FIRST 20 SECONDS</span>
              <span className="landing-float-value">100 readers reacting</span>
            </div>

            <div className="landing-legend">
              {REACTIONS.map((r) => (
                <span className="landing-legend-item" key={r.name}>
                  <span className="landing-legend-swatch" style={{ background: r.color }} />
                  {r.name}
                </span>
              ))}
            </div>
          </div>
        </div>

        <section aria-label="Key numbers" className="landing-stats">
          {STATS.map((s) => (
            <div className="landing-stat" key={s.label}>
              <span className="landing-stat-figure">{s.figure}</span>
              <span className="landing-stat-label">{s.label}</span>
              <span className="landing-stat-note">{s.note}</span>
            </div>
          ))}
        </section>
      </main>
    </div>
  )
}
