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
