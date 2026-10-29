// The /signin page. Its own layout rather than the modal chrome it
// started as: as a full page the modal's padding and button row read as
// cramped and arbitrary, because they were sized for an overlay sitting
// on top of content.
//
// Two states only: ask for the email, then say the link is on its way.
// No password, no separate verification step and nothing to remember --
// the link in the inbox is both the proof of a real person and the
// credential, which is the whole reason for choosing it.

import { useState } from 'react'

export default function SignInDialog({ auth, onClose }) {
  const [email, setEmail] = useState('')
  const [sending, setSending] = useState(false)

  async function handleSubmit(e) {
    e.preventDefault()
    if (sending) return
    setSending(true)
    try {
      await auth.sendLink(email)
    } finally {
      setSending(false)
    }
  }

  const sent = auth.sentTo

  // Nothing to sign in to: this deployment has no sign-in provider
  // configured, so the live page is simply open. Say so and offer the way
  // through, rather than presenting a form whose submit cannot work --
  // the link into this page is unconditional, so this state is reachable.
  if (!auth.enabled && auth.status !== 'loading') {
    return (
      <div className="signin-page">
        <div className="signin-card">
          <button type="button" className="signin-home" onClick={onClose}>
            <span className="signin-mark" aria-hidden="true">
              P
            </span>
            <span className="signin-wordmark">Panel</span>
          </button>
          <h1 className="signin-title">No sign-in needed here</h1>
          <p className="signin-body">
            This deployment has no sign-in provider configured, so the live panel is open to
            anyone who has the link.
          </p>
          <div className="signin-actions">
            <button
              type="button"
              className="signin-submit"
              onClick={() => window.location.assign('/live')}
            >
              Go to the live panel
            </button>
            <button type="button" className="signin-secondary" onClick={onClose}>
              Back to the demo
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="signin-page">
      <div className="signin-card">
        <button type="button" className="signin-home" onClick={onClose}>
          <span className="signin-mark" aria-hidden="true">
            P
          </span>
          <span className="signin-wordmark">Panel</span>
        </button>

        {sent ? (
          <>
            <h1 className="signin-title">Check your email</h1>
            <p className="signin-body">
              A sign-in link is on its way to <strong>{sent}</strong>. Opening it brings you
              straight to the panel, signed in. The link works once and then expires.
            </p>
            <div className="signin-actions">
              <button type="button" className="signin-submit" onClick={onClose}>
                Back to the demo
              </button>
              <button type="button" className="signin-secondary" onClick={auth.clearSent}>
                Use a different email
              </button>
            </div>
          </>
        ) : (
          <form onSubmit={handleSubmit}>
            <h1 className="signin-title">Run it on Nebius models</h1>
            <p className="signin-body">
              The demo runs on recorded data. To run the panel with Nebius models we need to know
              you&rsquo;re a person &mdash; every run spends real credits on a service anyone can
              reach.
            </p>
            <p className="signin-body">
              No password. We email you a link, you click it, you&rsquo;re in.
            </p>

            <label className="signin-label" htmlFor="signin-email">
              Email
            </label>
            <input
              id="signin-email"
              type="email"
              className="signin-input"
              placeholder="you@company.com"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="email"
              autoFocus
              required
            />

            {auth.error && (
              <p className="run-note run-note-error" role="alert">
                {auth.error}
              </p>
            )}

            <div className="signin-actions">
              <button type="submit" className="signin-submit" disabled={sending || !email.trim()}>
                {sending ? 'Sending…' : 'Email me a link'}
              </button>
              <button type="button" className="signin-secondary" onClick={onClose}>
                Back to the demo
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}
