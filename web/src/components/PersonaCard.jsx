// Pinned side card: full persona + full reaction.

const MOOD = {
  pos: { label: 'For it', bg: 'var(--sentiment-pos2)', fg: '#ffffff', tint: 'var(--tint-pos)' },
  neg: { label: 'Against', bg: 'var(--sentiment-neg2)', fg: '#ffffff', tint: 'var(--tint-neg)' },
  mid: { label: 'On the fence', bg: 'var(--sentiment-0)', fg: '#111827', tint: 'var(--tint-mid)' },
}

// The mockup's 3-tone mood language (For it / On the fence / Against),
// distinct from the 5-level sentiment color scale the tiles themselves use.
function moodTone(sentiment) {
  if (sentiment >= 1) return 'pos'
  if (sentiment <= -1) return 'neg'
  return 'mid'
}

function initials(name) {
  return name
    .split(' ')
    .map((w) => w[0])
    .join('')
}

function signed(n) {
  return n >= 0 ? `+${n}` : `${n}`
}

function Bar({ label, value }) {
  return (
    <div className="persona-card-bar-row">
      <div className="persona-card-bar-label-row">
        <span>{label}</span>
        <span>{value}</span>
      </div>
      <div className="persona-card-bar-track">
        <div className="persona-card-bar-fill" style={{ width: `${value}%` }} />
      </div>
    </div>
  )
}

function Field({ label, value }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="persona-card-section" style={{ gap: 4 }}>
      <span className="persona-card-section-label">{label}</span>
      <p style={{ margin: 0, fontSize: 13 }}>{value}</p>
    </div>
  )
}

export default function PersonaCard({ persona, reaction, onClose }) {
  if (!persona) return null

  const reacted = !!reaction && !reaction.raw_error
  const tone = reacted ? moodTone(reaction.sentiment) : null
  const mood = tone ? MOOD[tone] : null
  const first = persona.name.split(' ')[0]

  const big5 = [
    ['Openness', persona.openness],
    ['Conscientiousness', persona.conscientiousness],
    ['Extraversion', persona.extraversion],
    ['Agreeableness', persona.agreeableness],
    ['Neuroticism', persona.neuroticism],
  ]

  return (
    <div className="persona-card" onClick={(e) => e.stopPropagation()}>
      <div className="persona-card-header">
        <span
          className="persona-card-avatar"
          style={mood ? { background: mood.bg, color: mood.fg } : undefined}
        >
          {initials(persona.name)}
        </span>
        <div className="persona-card-identity">
          <span className="persona-card-name">{persona.name}</span>
          <span className="persona-card-subtitle">
            {persona.age} &middot; {persona.gender} &middot; {persona.occupation}
          </span>
        </div>
        <button className="persona-card-close" onClick={onClose} aria-label="Close">
          &times;
        </button>
      </div>

      <div className="persona-card-facts">
        <span className="persona-card-fact-chip">{persona.region}</span>
        <span className="persona-card-fact-chip">{persona.income_band} income</span>
        <span className="persona-card-fact-chip">{persona.household}</span>
      </div>

      {reacted ? (
        <div className="persona-card-reaction-box" style={{ background: mood.tint }}>
          <div className="persona-card-reaction-top">
            <span className="persona-card-reaction-label">REACTION</span>
            <span className="persona-card-chip" style={{ marginLeft: 'auto' }}>
              {mood.label} &middot; {signed(reaction.sentiment)}
            </span>
            <span className="persona-card-chip">{reaction.would_act ? 'Would act' : "Wouldn't act"}</span>
          </div>
          <p className="persona-card-quote">&ldquo;{reaction.gut_reaction}&rdquo;</p>
        </div>
      ) : (
        <div className="persona-card-waiting">
          {reaction?.raw_error
            ? `${first} didn't respond -- no reaction recorded.`
            : `No reaction yet. Run the panel to hear what ${first} thinks.`}
        </div>
      )}

      {reacted && (
        <>
          <Field label="TOP CONFUSION" value={reaction.top_confusion} />
          <Field label="TOP OBJECTION" value={reaction.top_objection} />
          <Field label="WOULD CHANGE THEIR MIND IF" value={reaction.what_would_change_my_mind} />
        </>
      )}

      <div className="persona-card-section">
        <span className="persona-card-section-label">HOW THEY READ</span>
        <Bar label="Tech comfort" value={persona.tech_comfort} />
        <Bar label="Skepticism" value={persona.skepticism} />
      </div>

      <div className="persona-card-section">
        <span className="persona-card-section-label">PERSONALITY</span>
        <div className="persona-card-big5-grid">
          {big5.map(([label, value]) => (
            <Bar key={label} label={label} value={value} />
          ))}
        </div>
      </div>

      <div className="persona-card-section">
        <span className="persona-card-section-label">CARES ABOUT</span>
        <div className="persona-card-values">
          {persona.values.map((v) => (
            <span key={v} className="persona-card-value-chip">
              {v}
            </span>
          ))}
        </div>
      </div>

      <div className="persona-card-fact-pair">
        <div className="persona-card-fact-pair-box">
          <span>WON OVER BY</span>
          <span>{persona.buying_triggers.join(', ')}</span>
        </div>
        <div className="persona-card-fact-pair-box">
          <span>PUT OFF BY</span>
          <span>{persona.pet_peeves.join(', ')}</span>
        </div>
      </div>

      <figure className="persona-card-voice-figure">
        <blockquote className="persona-card-voice-quote">&ldquo;{persona.voice}&rdquo;</blockquote>
        <figcaption className="persona-card-voice-caption">&mdash; {first}, in their own words</figcaption>
      </figure>
    </div>
  )
}
