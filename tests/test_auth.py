"""Token verification for panel/auth.py. No network calls.

These matter more than their size suggests: this is the only thing between
"anyone on the internet" and the endpoints that spend credits.
"""

import pytest

from panel import auth as fb


@pytest.fixture(autouse=True)
def _configured(monkeypatch):
    monkeypatch.setattr(fb, "FIREBASE_PROJECT_ID", "panel-test")
    monkeypatch.setattr(fb, "FIREBASE_API_KEY", "key")
    monkeypatch.setattr(fb, "FIREBASE_AUTH_DOMAIN", "panel-test.firebaseapp.com")


GOOD_ISS = "https://securetoken.google.com/panel-test"


def _stub_claims(monkeypatch, claims):
    """Stands in for google-auth, which has already checked the signature,
    the audience and the expiry by the time we see these claims."""
    import google.oauth2.id_token as mod

    monkeypatch.setattr(mod, "verify_firebase_token", lambda *a, **k: claims)


def test_a_valid_token_yields_the_uid_and_email(monkeypatch):
    _stub_claims(monkeypatch, {"iss": GOOD_ISS, "user_id": "u1", "email": "a@b.com"})
    assert fb._verify_sync("tok") == {"uid": "u1", "email": "a@b.com"}


def test_sub_is_accepted_when_user_id_is_absent(monkeypatch):
    _stub_claims(monkeypatch, {"iss": GOOD_ISS, "sub": "u2", "email": None})
    assert fb._verify_sync("tok")["uid"] == "u2"


def test_a_token_from_another_firebase_project_is_rejected(monkeypatch):
    """google-auth checks the audience, not the issuer. Without this check
    a token minted by any other Firebase project whose audience happened to
    match would be accepted."""
    _stub_claims(
        monkeypatch,
        {"iss": "https://securetoken.google.com/someone-elses-project", "user_id": "u1"},
    )
    assert fb._verify_sync("tok") is None


def test_a_token_with_no_subject_is_rejected(monkeypatch):
    _stub_claims(monkeypatch, {"iss": GOOD_ISS})
    assert fb._verify_sync("tok") is None


def test_verification_failure_is_not_an_exception(monkeypatch):
    """Expired tokens are routine -- they last an hour and a tab can be
    open far longer -- so a rejection must read as "not signed in" rather
    than turning into a 500."""
    import google.oauth2.id_token as mod

    def boom(*a, **k):
        raise ValueError("Token expired")

    monkeypatch.setattr(mod, "verify_firebase_token", boom)
    assert fb._verify_sync("tok") is None


@pytest.mark.parametrize(
    "header,expected",
    [
        ("Bearer abc", "abc"),
        ("bearer abc", "abc"),
        ("Bearer  abc  ", "abc"),
        ("Basic abc", None),
        ("Bearer", None),
        ("Bearer    ", None),
        ("abc", None),
        ("", None),
        (None, None),
    ],
)
def test_bearer_parsing(header, expected):
    assert fb.bearer_token(header) == expected


@pytest.mark.asyncio
async def test_verify_short_circuits_without_a_token_or_config(monkeypatch):
    assert await fb.verify(None) is None
    assert await fb.verify("") is None
    monkeypatch.setattr(fb, "FIREBASE_PROJECT_ID", None)
    # Not configured: never even attempts verification.
    import google.oauth2.id_token as mod

    monkeypatch.setattr(
        mod, "verify_firebase_token", lambda *a, **k: pytest.fail("must not verify")
    )
    assert await fb.verify("tok") is None


def test_public_config_never_leaks_when_disabled(monkeypatch):
    monkeypatch.setattr(fb, "FIREBASE_PROJECT_ID", None)
    assert fb.public_config() == {"enabled": False}


def test_public_config_carries_only_the_public_web_keys():
    cfg = fb.public_config()
    assert cfg == {
        "enabled": True,
        "apiKey": "key",
        "authDomain": "panel-test.firebaseapp.com",
        "projectId": "panel-test",
    }
