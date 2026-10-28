// Textarea / URL toggle, label field, example buttons -- step 1 ("What they
// read") of the setup card, section 6.1/6.8. The Run button (step 3) lives
// in App.jsx, not here, since step 2 (persona count) has to render between
// them -- MIN/MAX_TEXT_LENGTH are exported so App.jsx can compute the same
// canRun check for that button without duplicating the literal numbers.

export const MIN_TEXT_LENGTH = 50
export const MAX_TEXT_LENGTH = 20000

export default function ArtifactInput({
  mode,
  text,
  url,
  label,
  isRunning,
  examples = [],
  onModeChange,
  onTextChange,
  onUrlChange,
  onLabelChange,
  onSelectExample,
}) {
  return (
    <div className="setup-step">
      <div className="step-header">
        <span className="step-num">1</span>
        <span className="step-label">What they read</span>
        <div className="input-mode-toggle">
          <button
            type="button"
            className={mode === 'text' ? 'active' : ''}
            onClick={() => onModeChange('text')}
          >
            Text
          </button>
          <button
            type="button"
            className={mode === 'url' ? 'active' : ''}
            onClick={() => onModeChange('url')}
          >
            URL
          </button>
        </div>
      </div>

      {mode === 'text' ? (
        <textarea
          className="artifact-textarea"
          placeholder="Paste a landing page, ad, pricing page, or policy text..."
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          maxLength={MAX_TEXT_LENGTH}
        />
      ) : (
        <input
          type="url"
          className="artifact-url-input"
          placeholder="https://example.com/pricing"
          value={url}
          onChange={(e) => onUrlChange(e.target.value)}
        />
      )}

      <input
        type="text"
        className="artifact-label-input"
        placeholder="Label this run (optional)"
        value={label}
        onChange={(e) => onLabelChange(e.target.value)}
      />

      {examples.length > 0 && (
        <div className="examples-block">
          <span className="examples-heading">EXAMPLES</span>
          <div className="artifact-examples">
            {examples.map((ex) => (
              <button
                key={ex.id}
                type="button"
                className="artifact-example-button"
                disabled={isRunning}
                onClick={() => onSelectExample(ex)}
              >
                {ex.label}
              </button>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
