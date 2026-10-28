"""SSRF and size-limit guards on panel/fetch.py. No network calls.

POST /run is public and unauthenticated, and the text fetched from a URL
comes straight back to the caller in the Artifact, so these are the checks
standing between the demo URL and a full-read SSRF.
"""

import pytest

from panel.fetch import MAX_BYTES, UnsafeURLError, _assert_public_url, _read_capped


@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # AWS/GCP metadata by address
        "http://metadata.google.internal/computeMetadata/v1/",  # and by name
        "http://metadata.goog/",
        "http://127.0.0.1:8080/personas",  # the app talking to itself
        "http://localhost:8080/",
        "http://[::1]/",
        "http://10.0.0.5/admin",  # RFC1918
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://0.0.0.0/",
    ],
)
def test_private_and_metadata_targets_are_blocked(url):
    with pytest.raises(UnsafeURLError):
        _assert_public_url(url)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://x/", "ftp://x/", "data:text/plain,hi"])
def test_non_http_schemes_are_blocked(url):
    with pytest.raises(UnsafeURLError, match="only http and https"):
        _assert_public_url(url)


def test_a_url_with_no_host_is_blocked():
    with pytest.raises(UnsafeURLError, match="no host"):
        _assert_public_url("http:///just-a-path")


def test_a_public_host_is_allowed():
    # Token Factory is the one external host this project always reaches,
    # so it is resolvable wherever the suite runs.
    _assert_public_url("https://api.tokenfactory.nebius.com/v1")


class _FakeResponse:
    def __init__(self, chunks, headers=None, encoding="utf-8"):
        self._chunks = chunks
        self.headers = headers or {}
        self.encoding = encoding

    def iter_content(self, _size):
        return iter(self._chunks)


def test_read_capped_rejects_an_oversized_declared_length():
    resp = _FakeResponse([b"x"], headers={"Content-Length": str(MAX_BYTES + 1)})
    with pytest.raises(UnsafeURLError, match="larger than"):
        _read_capped(resp)


def test_read_capped_rejects_a_body_that_lies_about_its_length():
    """A chunked response declares no length at all, so the cap has to hold
    while reading, not just before it."""
    resp = _FakeResponse([b"x" * 8192] * (MAX_BYTES // 8192 + 2))
    with pytest.raises(UnsafeURLError, match="larger than"):
        _read_capped(resp)


def test_read_capped_passes_a_normal_body_through():
    assert _read_capped(_FakeResponse([b"hello ", b"world"])) == "hello world"


def test_read_capped_survives_undeclared_encoding():
    resp = _FakeResponse([b"caf\xc3\xa9"], encoding=None)
    assert _read_capped(resp) == "café"


def test_shared_address_space_is_not_public():
    """100.64.0.0/10 (carrier-grade NAT, some cloud internals) passed the
    old exclusion list."""
    from panel.fetch import _is_public_address

    assert not _is_public_address("100.64.1.1")
    assert not _is_public_address("::ffff:127.0.0.1")
    assert _is_public_address("8.8.8.8")


def test_tavily_failure_falls_back_to_the_direct_fetch(monkeypatch):
    from panel import fetch as F

    monkeypatch.setattr(F, "TAVILY_API_KEY", "k")

    def tavily_down(url):
        raise ValueError("Tavily returned no content")

    monkeypatch.setattr(F, "_fetch_via_tavily", tavily_down)
    monkeypatch.setattr(F, "_fetch_via_readability", lambda url: "direct text")
    assert F.fetch_url_text("https://example.com") == "direct text"


def test_an_unsafe_url_is_not_retried_without_tavily(monkeypatch):
    import pytest as _pytest

    from panel import fetch as F

    monkeypatch.setattr(F, "TAVILY_API_KEY", "k")

    def unsafe(url):
        raise F.UnsafeURLError("non-public")

    monkeypatch.setattr(F, "_fetch_via_tavily", unsafe)
    monkeypatch.setattr(F, "_fetch_via_readability", lambda url: _pytest.fail("must not fall back"))
    with _pytest.raises(F.UnsafeURLError):
        F.fetch_url_text("http://10.0.0.1")


def test_unresolvable_hosts_are_fatal_for_the_direct_fetch_only():
    """The direct fetch opens the connection itself, so it must prove the
    host is public. The Tavily path does not: Tavily fetches the page, so
    a host we cannot resolve is not a risk we are taking -- and failing
    closed there would break any deployment with restricted egress DNS
    that relies on Tavily, rejecting perfectly public pages."""
    import pytest as _pytest

    from panel.fetch import _assert_public_url

    nowhere = "https://this-host-does-not-exist-panel-test.invalid/x"
    with _pytest.raises(UnsafeURLError, match="could not resolve"):
        _assert_public_url(nowhere)
    # Permitted for the Tavily path.
    _assert_public_url(nowhere, require_resolvable=False)


def test_private_addresses_are_rejected_on_both_paths():
    """Relaxing the resolvability requirement must not relax the actual
    SSRF guard: a host that resolves to something private stays blocked
    even when Tavily would be the one fetching it."""
    import pytest as _pytest

    from panel.fetch import _assert_public_url

    for url in ("http://127.0.0.1/x", "http://10.0.0.1/x", "http://169.254.169.254/"):
        with _pytest.raises(UnsafeURLError):
            _assert_public_url(url, require_resolvable=False)
