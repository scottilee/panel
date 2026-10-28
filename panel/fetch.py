"""URL -> text. Tavily Extract when TAVILY_API_KEY is set, else requests +
readability-lxml. Section 5.6.
"""

from __future__ import annotations

import requests
from readability import Document

from panel.tf_client import env

TAVILY_API_KEY = env("TAVILY_API_KEY")
MAX_CHARS = 20_000
REQUEST_TIMEOUT_S = 15


def _fetch_via_tavily(url: str) -> str:
    resp = requests.post(
        "https://api.tavily.com/extract",
        json={"urls": [url]},
        headers={"Authorization": f"Bearer {TAVILY_API_KEY}"},
        timeout=REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    data = resp.json()
    results = data.get("results") or []
    if not results:
        raise ValueError(f"Tavily returned no content for {url}")
    return results[0]["raw_content"]


def _fetch_via_readability(url: str) -> str:
    resp = requests.get(
        url,
        timeout=REQUEST_TIMEOUT_S,
        headers={"User-Agent": "Mozilla/5.0 (compatible; PanelBot/1.0)"},
    )
    resp.raise_for_status()
    doc = Document(resp.text)
    # Document.summary() is HTML; strip tags for plain text the same way
    # pasted-text artifacts already arrive (section 3's Artifact.text is
    # always plain text, never HTML).
    from lxml import html as lxml_html

    tree = lxml_html.fromstring(doc.summary())
    text = tree.text_content()
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def fetch_url_text(url: str) -> str:
    """Raises requests.RequestException / ValueError on failure -- the
    caller (api/main.py) turns that into a user-facing error, same as any
    other unrecoverable run failure (section 5.4).
    """
    text = _fetch_via_tavily(url) if TAVILY_API_KEY else _fetch_via_readability(url)
    return text[:MAX_CHARS]
