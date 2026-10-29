// Magic-link sign-in, used only to gate live runs. Anonymous visitors get
// the whole app in mock mode; signing in is what spends credits.
//
// Three deliberate choices:
//
// 1. Config comes from GET /features at runtime, not from build-time
//    VITE_* vars. The container image is built once and deployed with
//    Terraform, so a build-time key would mean rebuilding the image to
//    change auth config -- and, worse, auth could only ever be enabled by
//    rebuilding. With this, an unset FIREBASE_API_KEY on the server means
//    `enabled: false` and the app behaves exactly as it did before auth
//    existed. That is the kill switch.
//
// 2. The Firebase SDK is imported dynamically, so a deployment with auth
//    disabled never downloads it.
//
// 3. A magic LINK, not a 6-digit code. Firebase sends the email itself;
//    an email code would mean a separate email provider, our own code
//    storage and expiry, and custom token minting -- all untested
//    infrastructure. The link is the version that can be trusted without
//    an inbox to test against.

import { useCallback, useEffect, useRef, useState } from 'react'

// Where the emailed link lands: always /live, never the page the request
// was made from. Signing in exists to unlock the real models, so the link
// should drop you straight onto the page that uses them rather than back
// on the form that sent it, or onto the recorded demo. Must be on the
// provider's authorized-domains list.
const RETURN_URL = () => `${window.location.origin}/live`

// The email is needed again when completing sign-in, because the link can
// be opened in a different tab than the one that requested it. Firebase's
// own documented approach.
const PENDING_EMAIL_KEY = 'panel.signin.email'

function readPendingEmail() {
  try {
    return window.localStorage.getItem(PENDING_EMAIL_KEY)
  } catch {
    return null
  }
}

function writePendingEmail(email) {
  try {
    if (email === null) window.localStorage.removeItem(PENDING_EMAIL_KEY)
    else window.localStorage.setItem(PENDING_EMAIL_KEY, email)
  } catch {
    // Private windows and blocked site data: sign-in still works as long
    // as the link is opened in the tab that asked for it.
  }
}

let sdkPromise = null

// Loaded once, on first use. Returns the module functions plus a live
// auth instance.
function loadSdk(config) {
  if (!sdkPromise) {
    sdkPromise = (async () => {
      const [{ initializeApp }, authMod] = await Promise.all([
        import('firebase/app'),
        import('firebase/auth'),
      ])
      const app = initializeApp({
        apiKey: config.apiKey,
        authDomain: config.authDomain,
        projectId: config.projectId,
      })
      return { ...authMod, auth: authMod.getAuth(app) }
    })()
  }
  return sdkPromise
}

/**
 * @param {{enabled: boolean, apiKey?: string, authDomain?: string, projectId?: string}|null} config
 *   The `auth` block from GET /features; null until it has loaded.
 */
export function useAuth(config) {
  const [user, setUser] = useState(null)
  // What the SDK has told us, which only matters once auth is configured.
  // 'loading' until we know whether anyone is signed in, so the UI never
  // flashes "signed out" at someone who is.
  const [sdkStatus, setSdkStatus] = useState('loading')
  const [sentTo, setSentTo] = useState(null)
  const [error, setError] = useState(null)
  const sdkRef = useRef(null)

  const enabled = !!config?.enabled

  // Derived rather than stored: "no config" and "config says off" are
  // facts about the props, so setting them as state in the effect below
  // only caused an extra render.
  const status = config === null ? 'loading' : enabled ? sdkStatus : 'disabled'

  useEffect(() => {
    if (config === null || !enabled) return
    let cancelled = false
    ;(async () => {
      try {
        const sdk = await loadSdk(config)
        if (cancelled) return
        sdkRef.current = sdk

        // Arriving back from the emailed link: finish sign-in before
        // subscribing, so the first state we report is the final one.
        if (sdk.isSignInWithEmailLink(sdk.auth, window.location.href)) {
          const email = readPendingEmail() ?? window.prompt('Confirm the email this link was sent to')
          if (email) {
            try {
              await sdk.signInWithEmailLink(sdk.auth, email, window.location.href)
              writePendingEmail(null)
              // Strip the one-time link parameters so a refresh or a
              // shared URL doesn't try to reuse a consumed link.
              window.history.replaceState({}, '', RETURN_URL())
            } catch (e) {
              if (!cancelled) setError(humanError(e))
            }
          }
        }

        sdk.onAuthStateChanged(sdk.auth, (u) => {
          if (cancelled) return
          setUser(u)
          setSdkStatus(u ? 'signed-in' : 'signed-out')
        })
      } catch (e) {
        if (!cancelled) {
          setError(humanError(e))
          setSdkStatus('error')
        }
      }
    })()
    return () => {
      cancelled = true
    }
  }, [config, enabled])

  const sendLink = useCallback(
    async (email) => {
      setError(null)
      const trimmed = email.trim()
      if (!trimmed.includes('@')) {
        setError('That does not look like an email address.')
        return false
      }
      try {
        const sdk = sdkRef.current ?? (await loadSdk(config))
        await sdk.sendSignInLinkToEmail(sdk.auth, trimmed, {
          url: RETURN_URL(),
          handleCodeInApp: true,
        })
        writePendingEmail(trimmed)
        setSentTo(trimmed)
        return true
      } catch (e) {
        setError(humanError(e))
        return false
      }
    },
    [config],
  )

  const signOut = useCallback(async () => {
    const sdk = sdkRef.current
    if (sdk) await sdk.signOut(sdk.auth)
    setSentTo(null)
  }, [])

  // Fresh on every call: ID tokens expire after an hour and a run can be
  // started long after the page loaded. The SDK caches and only refreshes
  // when it has to.
  const getToken = useCallback(async () => {
    const u = sdkRef.current?.auth?.currentUser
    return u ? u.getIdToken() : null
  }, [])

  return {
    enabled,
    status,
    signedIn: status === 'signed-in',
    email: user?.email ?? null,
    sentTo,
    error,
    sendLink,
    signOut,
    getToken,
    clearSent: useCallback(() => setSentTo(null), []),
  }
}

function humanError(e) {
  const code = e?.code ?? ''
  if (code === 'auth/invalid-email') return 'That does not look like an email address.'
  if (code === 'auth/invalid-action-code') {
    return 'That link has already been used or has expired. Request a new one.'
  }
  if (code === 'auth/too-many-requests') return 'Too many attempts. Try again in a few minutes.'
  if (code === 'auth/unauthorized-continue-uri' || code === 'auth/invalid-continue-uri') {
    return 'This site is not on the sign-in provider’s authorized domain list yet.'
  }
  if (code === 'auth/operation-not-allowed') {
    return 'Email link sign-in is not enabled for this project yet.'
  }
  return e?.message ?? 'Sign-in failed.'
}
