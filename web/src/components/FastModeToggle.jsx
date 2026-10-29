// The fast-mode switch in step 3, with an info button that explains the
// trade-off in plain words. Numbers are the live measurements behind
// panel/react.py's FAST_EXTRA_BODY (100 personas, three examples): negative
// share 35/53/29% -> 13/35/16% on pricing/ad/policy.

import { useEffect, useId, useRef, useState } from 'react'

export default function FastModeToggle({ fast, onChange, disabled = false, mock = false }) {
  const [open, setOpen] = useState(false)
  const infoId = useId()
  const wrapRef = useRef(null)

  // Closes on a click elsewhere or Escape, like any popover.
  useEffect(() => {
    if (!open) return undefined
    function onPointer(e) {
      if (!wrapRef.current?.contains(e.target)) setOpen(false)
    }
    function onKey(e) {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointer)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('pointerdown', onPointer)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <div className="fast-mode" ref={wrapRef}>
      <label className="fast-mode-switch">
        <input
          type="checkbox"
          role="switch"
          checked={fast}
          disabled={disabled}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span className="fast-mode-track" aria-hidden="true">
          <span className="fast-mode-thumb" />
        </span>
        <span className="fast-mode-label">Fast mode</span>
      </label>
      <button
        type="button"
        className="fast-mode-info"
        aria-label="What does fast mode do?"
        aria-expanded={open}
        aria-controls={infoId}
        onClick={() => setOpen((o) => !o)}
      >
        i
      </button>
      {open && (
        <div id={infoId} className="fast-mode-pop" role="note">
          <p>
            Normally each persona <strong>thinks before answering</strong>: the AI (NVIDIA Nemotron Nano) privately
            reasons through your copy, then replies. Fast mode skips that step, so each persona answers on gut
            instinct.
          </p>
          <p>
            <strong>You gain speed.</strong> In our tests the panel read about 3 to 5 times faster: under 20 seconds
            for 100 people instead of a minute or two. Answers also stopped failing.
          </p>
          <p>
            <strong>You give up clear opinions.</strong> Answering on instinct, far more people sit on the fence. On our
            pricing example the share reacting negatively fell from 35% to 13%, and on the ad twice as many said they
            would act (16 in 100 instead of 8).
          </p>
          <p>
            So the percentages in a fast run come out lower and can&apos;t be compared with a normal run. Good for quick
            drafts; leave it off for numbers you plan to quote.
          </p>
          {mock && (
            <p className="fast-mode-pop-note">
              Demo mode: these reactions are simulated, so here fast mode only speeds up the playback and moves people
              onto the fence by about the amount we measured.
            </p>
          )}
        </div>
      )}
    </div>
  )
}
