// Verdict section (post-run), new per the "4C" mockup: headline, big
// would-act %, Winning/Losing boxes, dark "try this edit" panel. Sits
// above the Reactions card; Findings.jsx (the flat claim list) sits below
// it -- together they replace the single findings-card-grid this synthesis
// data used to render through.

export default function Verdict({ synthesis, runLabel, wouldActPct, onApplyAndCompare, applyDisabled }) {
  if (!synthesis) return null
  const edit = synthesis.highest_leverage_edit

  return (
    <section aria-label="Verdict" className="verdict-card">
      <div className="verdict-top">
        <div className="verdict-headline-block">
          <span className="verdict-eyebrow">THE VERDICT &middot; {runLabel}</span>
          <h1 className="verdict-headline">{synthesis.headline}</h1>
        </div>
        <div className="verdict-bigact">
          <span className="verdict-bigact-value">{Math.round(wouldActPct)}%</span>
          <span className="verdict-bigact-label">would act</span>
        </div>
      </div>

      <div className="verdict-badges">
        <div className="verdict-badge verdict-badge-winning">
          <span>WINNING</span>
          <span>{synthesis.who_you_are_winning}</span>
        </div>
        <div className="verdict-badge verdict-badge-losing">
          <span>LOSING</span>
          <span>{synthesis.who_you_are_losing}</span>
        </div>
      </div>

      <div className="verdict-edit-panel">
        <div className="verdict-edit-text">
          <span className="verdict-edit-eyebrow">TRY THIS EDIT &middot; {edit.predicted_flips} PREDICTED FLIPS</span>
          <span className="verdict-edit-change">
            <span className="verdict-edit-before">{edit.current_text}</span>
            {'  ->  '}
            <strong className="verdict-edit-after">{edit.proposed_text}</strong>
          </span>
          <span className="verdict-edit-why">{edit.rationale}</span>
        </div>
        <button
          type="button"
          className="verdict-apply-button"
          onClick={onApplyAndCompare}
          disabled={applyDisabled}
        >
          Apply and compare
        </button>
      </div>
    </section>
  )
}
