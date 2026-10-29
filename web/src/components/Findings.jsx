// Findings (post-run): one row per claim -- severity badge, claim,
// segment chip, evidence avatars, and the share of that segment who
// reacted negatively. The headline, edit and winning/losing lines live in
// Verdict.jsx.

const SEVERITY_LABEL = { high: 'High', medium: 'Medium', low: 'Low' }

function EvidenceAvatars({ evidence, onHoverStart, onHoverEnd }) {
  return (
    <div className="finding-evidence">
      {evidence.map((id) => (
        <span
          key={id}
          className="finding-evidence-avatar"
          onMouseEnter={() => onHoverStart(evidence)}
          onMouseLeave={onHoverEnd}
          title={id}
        >
          {id.replace(/^p0*/, '')}
        </span>
      ))}
    </div>
  )
}

// affected_pct is the share of the segment's members who reacted
// negatively, recounted from the real reactions. segment_size/segment_pct
// are null when the filter couldn't be recounted, in which case the
// percentage is the model's own estimate and is labelled as one.
function segmentCaption(finding) {
  if (finding.segment_size == null) return 'estimated, not recounted'
  const people = `${finding.segment_size} ${finding.segment_size === 1 ? 'person' : 'people'}`
  return finding.segment_pct == null ? people : `${people} · ${Math.round(finding.segment_pct)}% of panel`
}

// The panel-wide figure the segment's share should be read against. Old
// recordings predate it, so a missing baseline just isn't drawn.
function baselineCaption(finding, overall) {
  if (overall == null || finding.segment_size == null) return null
  const diff = finding.affected_pct - overall
  const relation = diff === 0 ? 'same as' : `${Math.abs(diff)} points ${diff > 0 ? 'above' : 'below'}`
  return `${relation} the whole panel (${overall}%)`
}

function FindingRow({ finding, overall, onSegmentClick, onEvidenceHoverStart, onEvidenceHoverEnd }) {
  const baseline = baselineCaption(finding, overall)
  return (
    <article className="finding-row">
      <span className={`finding-row-severity finding-row-severity-${finding.severity}`}>
        {(SEVERITY_LABEL[finding.severity] ?? finding.severity).toUpperCase()}
      </span>
      <div className="finding-row-main">
        <p className="finding-row-claim">{finding.claim}</p>
        <div className="finding-row-meta">
          <button
            type="button"
            className="finding-segment-chip"
            onClick={() => onSegmentClick(finding)}
            title="Show exactly this segment in the grid"
          >
            {finding.affected_segment}
          </button>
          <EvidenceAvatars
            evidence={finding.evidence}
            onHoverStart={onEvidenceHoverStart}
            onHoverEnd={onEvidenceHoverEnd}
          />
        </div>
      </div>
      <div className="finding-row-pct-wrap">
        <span className="finding-row-pct">{finding.affected_pct}%</span>
        <span className="finding-row-pct-label">of this segment reacted negatively</span>
        <div className="finding-row-bar">
          <div className="finding-row-bar-fill" style={{ width: `${finding.affected_pct}%` }} />
          {baseline && (
            <div
              className="finding-row-bar-baseline"
              style={{ left: `${overall}%` }}
              data-tip={`The whole panel sits at ${overall}% — the same mark on every finding, so each bar can be read against it`}
              aria-label={`Whole panel baseline, ${overall}% reacted negatively`}
              role="img"
            />
          )}
        </div>
        {baseline && <span className="finding-row-pct-label finding-row-baseline">{baseline}</span>}
        <span className="finding-row-pct-label">{segmentCaption(finding)}</span>
      </div>
    </article>
  )
}

export default function Findings({ synthesis, onSegmentClick, onEvidenceHoverStart, onEvidenceHoverEnd }) {
  if (!synthesis) return null

  return (
    <section aria-label="Findings" className="findings-card">
      {synthesis.findings.map((finding, i) => (
        <FindingRow
          key={i}
          finding={finding}
          overall={synthesis.overall_negative_pct}
          onSegmentClick={onSegmentClick}
          onEvidenceHoverStart={onEvidenceHoverStart}
          onEvidenceHoverEnd={onEvidenceHoverEnd}
        />
      ))}
    </section>
  )
}
