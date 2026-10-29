"""Verifies Firebase ID tokens, to gate the calls that spend credits.

Only live runs are gated. The landing page, the persona pool, the examples,
the pre-submission check and every cached replay stay open, because a judge
following a link should see the whole app work without an account -- and
none of that spends anything.

Deliberately `google-auth` rather than `firebase-admin`. All we need is
"is this ID token real, unexpired, and issued for our project", which is
one function; firebase-admin brings grpcio and a service-account
initialisation dance for no benefit here, and this project has already
been bitten once by a dependency with no Python 3.14 wheel.

Configuration is all-or-nothing and absent by default: with
FIREBASE_PROJECT_ID unset, `enabled()` is False, no endpoint is gated, and
the app behaves exactly as it did before this file existed. That is the
intended kill switch -- unset one variable and redeploy.
"""

from __future__ import annotations

import asyncio

from panel.config import env

# The project the token must have been issued for. Also the audience claim
# we check, which is what stops a token minted for someone else's Firebase
# project being accepted here.
FIREBASE_PROJECT_ID = env("FIREBASE_PROJECT_ID")
# Public by design -- these two are embedded in any Firebase web app and
# are not secrets. They are served to the frontend by GET /features so the
# container image doesn't have to be rebuilt to configure auth.
FIREBASE_API_KEY = env("FIREBASE_API_KEY")
FIREBASE_AUTH_DOMAIN = env("FIREBASE_AUTH_DOMAIN")


def enabled() -> bool:
    """True only when the whole set is configured. A half-configured
    deployment must not half-gate: the frontend could not sign anyone in,
    and gating would lock everyone out of live runs with no way back."""
    return bool(FIREBASE_PROJECT_ID and FIREBASE_API_KEY and FIREBASE_AUTH_DOMAIN)


def public_config() -> dict:
    """What GET /features hands the frontend."""
    if not enabled():
        return {"enabled": False}
    return {
        "enabled": True,
        "apiKey": FIREBASE_API_KEY,
        "authDomain": FIREBASE_AUTH_DOMAIN,
        "projectId": FIREBASE_PROJECT_ID,
    }


class _Lazy:
    """google-auth's transport is built once and reused; it caches Google's
    signing certificates, so only the first verification makes a network
    call."""

    request = None


def _verify_sync(token: str) -> dict | None:
    from google.auth.transport import requests as ga_requests
    from google.oauth2 import id_token

    if _Lazy.request is None:
        _Lazy.request = ga_requests.Request()
    try:
        claims = id_token.verify_firebase_token(
            token, _Lazy.request, audience=FIREBASE_PROJECT_ID
        )
    except Exception as e:  # noqa: BLE001 -- any failure is "not signed in"
        # Expired tokens are routine (they last an hour and a page can be
        # open longer), so this is not an error worth a stack trace.
        print(f"auth: rejected a token: {type(e).__name__}: {e}", flush=True)
        return None
    if not claims:
        return None
    # verify_firebase_token checks the audience and expiry but not the
    # issuer, and the issuer is what ties the token to our project.
    if claims.get("iss") != f"https://securetoken.google.com/{FIREBASE_PROJECT_ID}":
        print(f"auth: rejected a token with issuer {claims.get('iss')!r}", flush=True)
        return None
    uid = claims.get("user_id") or claims.get("sub")
    if not uid:
        return None
    return {"uid": uid, "email": claims.get("email")}


async def verify(token: str | None) -> dict | None:
    """{'uid', 'email'} for a valid token, else None. Never raises: callers
    decide what an unauthenticated request means, and a verification that
    blew up would otherwise turn a signed-out user into a 500."""
    if not token or not enabled():
        return None
    return await asyncio.to_thread(_verify_sync, token)


def bearer_token(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()
