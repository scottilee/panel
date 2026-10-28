// Pinned side card: full persona + full reaction, section 6.3.

function Field({ label, value }) {
  if (value === null || value === undefined || value === '') return null
  return (
    <div className="persona-card-field">
      <span className="persona-card-field-label">{label}</span>
      <span className="persona-card-field-value">{value}</span>
    </div>
  )
}

export default function PersonaCard({ persona, reaction, onClose }) {
  if (!persona) return null

  return (
    <div className="persona-card" onClick={(e) => e.stopPropagation()}>
      <button className="persona-card-close" onClick={onClose} aria-label="Close">
        &times;
      </button>
      <h3 className="persona-card-name">{persona.name}</h3>
      <div className="persona-card-subtitle">
        {persona.age} &middot; {persona.gender} &middot; {persona.occupation}
      </div>
      <div className="persona-card-subtitle">
        {persona.location} ({persona.region}) &middot; {persona.income_band}
      </div>
      <div className="persona-card-subtitle">{persona.household}</div>

      <div className="persona-card-scores">
        <Field label="tech comfort" value={`${persona.tech_comfort}/100`} />
        <Field label="skepticism" value={`${persona.skepticism}/100`} />
        <Field label="openness" value={`${persona.openness}/100`} />
        <Field label="conscientiousness" value={`${persona.conscientiousness}/100`} />
        <Field label="extraversion" value={`${persona.extraversion}/100`} />
        <Field label="agreeableness" value={`${persona.agreeableness}/100`} />
        <Field label="neuroticism" value={`${persona.neuroticism}/100`} />
      </div>

      <Field label="values" value={persona.values?.join(', ')} />
      <Field label="buying triggers" value={persona.buying_triggers?.join(', ')} />
      <Field label="pet peeves" value={persona.pet_peeves?.join(', ')} />
      <blockquote className="persona-card-voice">&ldquo;{persona.voice}&rdquo;</blockquote>

      <hr className="persona-card-divider" />

      {!reaction && <div className="persona-card-pending">No reaction yet.</div>}
      {reaction?.raw_error && <div className="persona-card-pending">(no response)</div>}
      {reaction && !reaction.raw_error && (
        <>
          <Field label="gut reaction" value={`"${reaction.gut_reaction}"`} />
          <Field label="sentiment" value={reaction.sentiment} />
          <Field label="would act" value={reaction.would_act ? 'yes' : 'no'} />
          <Field label="top confusion" value={reaction.top_confusion} />
          <Field label="top objection" value={reaction.top_objection} />
          <Field label="what would change their mind" value={reaction.what_would_change_my_mind} />
        </>
      )}
    </div>
  )
}
