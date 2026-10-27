// Textarea / URL toggle, label field, example buttons. Stage 1, Agent B.
//
// Example buttons and the post-run Compare button (section 6.1) are stage 3
// scope -- this stage covers the textarea/URL toggle, label, and Run only.

const MIN_TEXT_LENGTH = 50
const MAX_TEXT_LENGTH = 20000

export default function ArtifactInput({
  mode,
  text,
  url,
  label,
  isRunning,
  onModeChange,
  onTextChange,
  onUrlChange,
  onLabelChange,
  onRun,
}) {
  const canRun =
    !isRunning &&
    (mode === 'text'
      ? text.trim().length >= MIN_TEXT_LENGTH && text.length <= MAX_TEXT_LENGTH
      : url.trim().length > 0)

  return (
    <div className="artifact-input">
      <div className="artifact-input-mode-toggle">
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
        placeholder="Label (optional)"
        value={label}
        onChange={(e) => onLabelChange(e.target.value)}
      />

      <button type="button" className="run-button" disabled={!canRun} onClick={onRun}>
        {isRunning ? 'Running...' : 'Run'}
      </button>
    </div>
  )
}
