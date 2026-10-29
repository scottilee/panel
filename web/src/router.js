// Four pages, so a few lines of routing rather than a router dependency.
//
// The route decides whether the panel is real, not the auth state:
//
//   /        landing
//   /demo    the app on recorded data -- always, signed in or not
//   /live    the app on Nebius models, which is what spends credits
//   /signin  asks for an email, and the emailed link lands on /live
//
// Keying the mode to the route rather than to "is someone signed in" is
// what makes /demo honest: it is the same page for everyone, and it can
// never quietly start making real calls because of who is looking at it.
// '/live' deliberately avoids '/run', which is an API route.
//
// The History API rather than hash routing, because /demo/ has to be a
// real, shareable, refreshable URL -- api/main.py serves index.html for it
// so a direct hit works, and Vite's dev server does the same.

import { useEffect, useState } from 'react'

export const LANDING = 'landing'
export const DEMO = 'demo'
export const LIVE = 'live'
export const SIGNIN = 'signin'

function routeFor(pathname) {
  // Trailing slash optional, so /demo and /demo/ are the same page.
  if (/^\/demo\/?$/.test(pathname)) return DEMO
  if (/^\/live\/?$/.test(pathname)) return LIVE
  if (/^\/signin\/?$/.test(pathname)) return SIGNIN
  return LANDING
}

export function useRoute() {
  const [route, setRoute] = useState(() =>
    typeof window === 'undefined' ? LANDING : routeFor(window.location.pathname),
  )

  // Back and forward have to work: a judge who clicks "See it run" and
  // then hits back should land on the landing page, not leave the site.
  useEffect(() => {
    const onPop = () => setRoute(routeFor(window.location.pathname))
    window.addEventListener('popstate', onPop)
    return () => window.removeEventListener('popstate', onPop)
  }, [])

  function navigate(path) {
    if (window.location.pathname === path) return
    window.history.pushState({}, '', path)
    setRoute(routeFor(path))
    window.scrollTo(0, 0)
  }

  return { route, navigate }
}
