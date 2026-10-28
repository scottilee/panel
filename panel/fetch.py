"""URL -> text. Tavily Extract when TAVILY_API_KEY is set, else requests +
readability-lxml. Section 5.6.

This module is reachable from the public, unauthenticated POST /run, and
what it returns is handed straight back to the caller in the Artifact --
so a naive requests.get here is a full-read SSRF, not a blind one. Hence
_assert_public_url below: scheme allowlist, resolved-address checks
against every private range, manual redirect following with the same
checks on each hop, and a hard byte cap on the body.

Known limit, stated rather than papered over: the address check happens at
resolve time and the connection is made moments later, so a DNS name whose
record flips between the two (rebinding) is not defended against. Closing
that needs connection-level pinning, which requests does not expose.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse

import requests
from readability import Document

from panel.tf_client import env

TAVILY_API_KEY = env("TAVILY_API_KEY")
MAX_CHARS = 20_000
REQUEST_TIMEOUT_S = 15
# Body cap. resp.text used to materialize the whole response before
# MAX_CHARS truncated it, so a large URL was read entirely into a 1Gi
# container to produce 20k characters. 2 MB is far more than any article
# whose readable text fits in MAX_CHARS.
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3

_ALLOWED_SCHEMES = ("http", "https")

# Hostnames that resolve publicly (or not at all) but are special-cased by
# cloud metadata services. Checked by name as well as by address because
# the name is the documented way to reach them.
_BLOCKED_HOSTS = {
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
}


class UnsafeURLError(ValueError):
    """The URL points somewhere a public web form must not reach."""


def _is_public_address(addr: str) -> bool:
    ip = ipaddress.ip_address(addr)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _assert_public_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeURLError(f"only http and https URLs are supported, not {parsed.scheme!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    if host.lower().rstrip(".") in _BLOCKED_HOSTS:
        raise UnsafeURLError("that host is not allowed")

    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80))
    except socket.gaierror as e:
        raise UnsafeURLError(f"could not resolve {host}") from e

    addresses = {info[4][0] for info in infos}
    if not addresses:
        raise UnsafeURLError(f"could not resolve {host}")
    # Every resolved address must be public, not just the first: a host
    # with one public and one loopback A record would otherwise be a way
    # straight through this check.
    for addr in addresses:
        if not _is_public_address(addr):
            raise UnsafeURLError("that host resolves to a non-public address")


def _read_capped(resp: requests.Response) -> str:
    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES:
        raise UnsafeURLError(f"response is larger than {MAX_BYTES} bytes")
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(8192):
        total += len(chunk)
        if total > MAX_BYTES:
            raise UnsafeURLError(f"response is larger than {MAX_BYTES} bytes")
        chunks.append(chunk)
    body = b"".join(chunks)
    return body.decode(resp.encoding or "utf-8", errors="replace")


def _get_checked(url: str, **kwargs) -> requests.Response:
    """GET with SSRF checks applied to the original URL and to every
    redirect hop. requests' own allow_redirects would follow a 302 into
    169.254.169.254 without re-checking anything, so redirects are
    followed here instead.
    """
    for _ in range(MAX_REDIRECTS + 1):
        _assert_public_url(url)
        resp = requests.get(
            url,
            timeout=REQUEST_TIMEOUT_S,
            allow_redirects=False,
            stream=True,
            **kwargs,
        )
        if resp.is_redirect or resp.is_permanent_redirect:
            location = resp.headers.get("Location")
            resp.close()
            if not location:
                raise UnsafeURLError("redirect without a Location header")
            url = requests.compat.urljoin(url, location)
            continue
        return resp
    raise UnsafeURLError(f"more than {MAX_REDIRECTS} redirects")


def _fetch_via_tavily(url: str) -> str:
    # Tavily is a fixed, trusted endpoint, so the SSRF checks don't apply
    # to this request -- but the submitted URL still gets validated, since
    # Tavily would otherwise happily fetch it on our behalf.
    _assert_public_url(url)
    resp = requests.post(
        "https://api.tavily.com/extract",
        json={"urls": [url]},
        headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
        timeout=REQUEST_TIMEOUT_S,
        stream=True,
    )
    resp.raise_for_status()
    import json as _json

    data = _json.loads(_read_capped(resp))
    results = data.get("results") or []
    if not results:
        raise ValueError(f"Tavily returned no content for {url}")
    return results[0]["raw_content"]


def _fetch_via_readability(url: str) -> str:
    resp = _get_checked(url, headers={"User-Agent": "Mozilla/5.0 (compatible; PanelBot/1.0)"})
    try:
        resp.raise_for_status()
        html = _read_capped(resp)
    finally:
        resp.close()
    doc = Document(html)
    # Document.summary() is HTML; strip tags for plain text the same way
    # pasted-text artifacts already arrive (section 3's Artifact.text is
    # always plain text, never HTML).
    from lxml import html as lxml_html

    tree = lxml_html.fromstring(doc.summary())
    text = tree.text_content()
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def fetch_url_text(url: str) -> str:
    """Raises UnsafeURLError / requests.RequestException / ValueError on
    failure -- the caller (api/main.py) turns that into a user-facing
    error, same as any other unrecoverable run failure (section 5.4).
    """
    text = _fetch_via_tavily(url) if TAVILY_API_KEY else _fetch_via_readability(url)
    return text[:MAX_CHARS]
