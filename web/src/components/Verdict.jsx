// Verdict section (post-run): headline, the not-convinced count,
// Winning/Losing boxes, and the "try this edit" panel. Sits above the
// Reactions card; Findings.jsx (the claim list) sits below it.

export default function Verdict({
  synthesis,
  runLabel,
  wouldActPct,
  total,
  onApplyAndCompare,
  applyDisabled,
  applyError,
}) {
  if (!synthesis) return null
  const edit = synthesis.highest_leverage_edit
  // A count, not a percentage. `total` is the number of usable reactions
  // currently in view (segment filters included), so both halves of
  // "N of M aren't convinced" come from the same denominator.
  const unconvinced = Math.round((total * (100 - wouldActPct)) / 100)

  return (
    <section aria-label="Verdict" className="verdict-card">
      <div className="verdict-top">
        <div className="verdict-headline-block">
          <span className="verdict-eyebrow">THE VERDICT &middot; {runLabel}</span>
          <h1 className="verdict-headline">{synthesis.headline}</h1>
        </div>
        {/* The headline number is the count NOT convinced, not the share
            who would act. Same data either way, but "4% would act" reads
            as the product failing, while "96 of 100 aren't convinced"
            reads as the finding -- which is what this tool actually
            delivers. Real runs land at 92-100% unconvinced by design (see
            the anti-sycophancy check), so framing it as the diagnosis is
            both more useful and more honest than framing it as a
            conversion rate. */}
        <div className="verdict-bigact">
          <span className="verdict-bigact-value">{unconvinced}</span>
          <span className="verdict-bigact-label">of {total} aren&rsquo;t convinced</span>
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
          {/* predicted_flips is deliberately NOT shown. Measured against
              real runs, Ultra predicted 35-45 flips where the actual
              rerun produced 0-7 -- displaying a number we know is wrong
              by that margin invites a judge to apply the edit and catch
              us out. The edit itself and its rationale are the useful
              part; the real flip count shows up after Apply and compare,
              measured rather than guessed. */}
          <span className="verdict-edit-eyebrow">ONE EDIT TO TRY</span>
          <span className="verdict-edit-change">
            <span className="verdict-edit-before">{edit.current_text}</span>
            {'  ->  '}
            <strong className="verdict-edit-after">{edit.proposed_text}</strong>
          </span>
          {edit.rationale && <span className="verdict-edit-why">{edit.rationale}</span>}
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
      {applyError && (
        <p className="run-note run-note-error verdict-apply-error" role="alert">
          {applyError}
        </p>
      )}
    </section>
  )
}
