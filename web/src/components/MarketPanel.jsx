// Market benchmark: the same panel read the user's copy and two or three
// competitor pages found via Tavily, and this reports what confused
// readers on all of them versus only on theirs.
//
// Note what is deliberately absent: any head-to-head conversion number.
// A reader who would act on a competitor's page may be responding to
// their offer, not their writing, so a scoreboard would be a confounded
// claim. Confusions and objections are attributable to the page.
//
// Built from the same pieces as Verdict.jsx -- the eyebrow, the big
// headline, the winning/losing badge pair -- because this is the same
// kind of statement (here is the finding, here are the two sides of it)
// and an invented visual language for it just read as a different app.

import { useState } from 'react'

const STATUS_LABEL = {
  pending: 'Starting',
  searching: 'Looking for comparable pages',
  reading: 'The panel is reading each page',
  comparing: 'Comparing what readers said',
}

// Composed from the counts rather than written by a model: this is the
// one line a reader takes away, so it should be arithmetic.
function headlineFor(comparison) {
  const shared = comparison.shared_confusions.length + comparison.shared_objections.length
  const mine = comparison.only_yours_confusions.length + comparison.only_yours_objections.length
  const others = comparison.pages.length - 1
  if (!shared && !mine) return 'Readers raised nothing that lined up across these pages.'
  if (!mine) {
    return `Every problem readers hit on your page, they hit on ${
      others === 1 ? 'the other page' : 'the other pages'
    } too.`
  }
  if (!shared) {
    return `${mine} ${mine === 1 ? 'thing' : 'things'} tripped readers up on your page and on none of the others.`
  }
  return `${shared} ${shared === 1 ? 'problem is' : 'problems are'} shared across the category; ${mine} ${
    mine === 1 ? 'is' : 'are'
  } yours alone.`
}

function ThemeRows({ themes, emptyNote, totalPages }) {
  if (!themes.length) return <p className="market-note">{emptyNote}</p>
  return (
    <ul className="market-themes">
      {themes.map((t) => (
        <li key={t.theme} className="market-theme-row">
          <span className="market-theme">{t.theme}</span>
          <span className="market-theme-count">
            {t.pages.length > 1 ? `${t.pages.length} of ${totalPages} pages` : t.pages[0]}
          </span>
        </li>
      ))}
    </ul>
  )
}

export default function MarketPanel({
  status,
  query,
  pages,
  comparison,
  error,
  onStart,
  onDismiss,
  disabled,
  isMock = false,
}) {
  const [openPage, setOpenPage] = useState(null)

  if (!status) {
    return (
      <section aria-label="Market comparison" className="market-card market-card-idle">
        <div className="market-pitch">
          <span className="market-eyebrow">THE MARKET</span>
          <h2 className="market-idle-title">Compare against the market</h2>
          <p className="market-note">
            Find two or three competitor pages for the same thing and put the
            same readers in front of those too. Separates what every page in
            your category gets wrong from what only yours does.
            {isMock && ' In mock mode this is synthetic — nothing is searched or read.'}
          </p>
        </div>
        <button type="button" className="market-start" onClick={onStart} disabled={disabled}>
          Read the market
        </button>
      </section>
    )
  }

  const busy = status in STATUS_LABEL
  const totalPages = pages.length

  return (
    <section aria-label="Market comparison" className="market-card">
      <div className="market-top">
        <div className="market-headline-block">
          <span className="market-eyebrow">
            THE MARKET
            {query && <span className="market-query"> &middot; {isMock ? query : `“${query}”`}</span>}
          </span>
          {comparison ? (
            <h2 className="market-headline">{headlineFor(comparison)}</h2>
          ) : busy ? (
            <h2 className="market-headline market-headline-busy">
              <span className="run-spinner market-spinner" aria-hidden="true" />
              {STATUS_LABEL[status]}
            </h2>
          ) : null}
        </div>
        {/* While it runs this also stops the server-side work (api.js). */}
        <button type="button" className="market-dismiss" onClick={onDismiss}>
          {busy ? 'Stop' : 'Dismiss'}
        </button>
      </div>

      {status === 'no_pages' && (
        <p className="market-note">
          No comparable pages found. Searching this category returns mostly
          articles and roundups rather than companies’ own pages, so there is
          nothing honest to compare against.
        </p>
      )}

      {status === 'error' && (
        <p className="run-note run-note-error" role="alert">
          {error || 'The market comparison failed.'}
        </p>
      )}

      {pages.length > 0 && (
        <div className="market-pages">
          {pages.map((p) => (
            <button
              key={p.label}
              type="button"
              className={`market-page${p.is_yours ? ' market-page-yours' : ''}`}
              onClick={() => setOpenPage(p)}
            >
              <span className="market-page-label">{p.label}</span>
              <span className="market-page-meta">
                {p.panel_size} readers &middot; what they read
              </span>
            </button>
          ))}
        </div>
      )}

      {comparison && (
        <div className="market-badges">
          <div className="market-badge market-badge-shared">
            <span className="market-badge-title">The whole category</span>
            <span className="market-badge-sub">
              Raised on yours and on theirs — not something you can edit away.
            </span>
            <ThemeRows
              themes={[...comparison.shared_confusions, ...comparison.shared_objections]}
              emptyNote="Nothing lined up across pages."
              totalPages={totalPages}
            />
          </div>
          <div className="market-badge market-badge-yours">
            <span className="market-badge-title">Only your page</span>
            <span className="market-badge-sub">
              Raised on yours and none of theirs — the part worth fixing.
            </span>
            <ThemeRows
              themes={[...comparison.only_yours_confusions, ...comparison.only_yours_objections]}
              emptyNote="Nothing tripped readers up on yours alone."
              totalPages={totalPages}
            />
          </div>
        </div>
      )}

      {openPage && (
        <div className="modal-backdrop" onClick={() => setOpenPage(null)} role="presentation">
          <div
            className="modal modal-wide"
            role="dialog"
            aria-modal="true"
            aria-labelledby="market-page-title"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 className="modal-title" id="market-page-title">
              {openPage.label}
            </h2>
            <p className="modal-body">
              {openPage.is_yours
                ? `The copy you submitted. ${openPage.panel_size} readers saw this.`
                : `Exactly what ${openPage.panel_size} readers were shown. Extracted from the page, so the formatting is gone and some navigation text may have come along.`}
              {openPage.url && (
                <>
                  {' '}
                  <a href={openPage.url} target="_blank" rel="noreferrer noopener">
                    Open the original
                  </a>
                </>
              )}
            </p>
            <pre className="market-page-text">{openPage.text || '(no text captured)'}</pre>
            <div className="modal-actions">
              <button type="button" className="modal-cancel" onClick={() => setOpenPage(null)}>
                Close
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
