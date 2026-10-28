// Findings (post-run flat list), section 6.6, restyled per the "4C"
// mockup: a flat list of claim rows (severity badge, claim + segment chip
// + evidence avatars, big percentage + progress bar) below the Reactions
// card. The headline, highest-leverage edit, and losing/winning lines now
// live in Verdict.jsx, above the Reactions card -- this component only
// renders the three finding rows.

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

function FindingRow({ finding, onSegmentClick, onEvidenceHoverStart, onEvidenceHoverEnd }) {
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
            onClick={() => onSegmentClick(finding.affected_segment_filter)}
            title="Apply this segment as a filter"
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
        <div className="finding-row-bar">
          <div className="finding-row-bar-fill" style={{ width: `${finding.affected_pct}%` }} />
        </div>
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
          onSegmentClick={onSegmentClick}
          onEvidenceHoverStart={onEvidenceHoverStart}
          onEvidenceHoverEnd={onEvidenceHoverEnd}
        />
      ))}
    </section>
  )
}
