"""Common shape for every ATS adapter.

23 applicant tracking systems showed up across 533 real opportunities. They
look like 23 problems. They are three:

  FAMILY 1  public JSON board API      documented or near enough, returns clean JSON
  FAMILY 2  JSON embedded in the HTML  the page is a shell, the data is in a script tag
  FAMILY 3  undocumented internal API  needs an exact path, header or method to answer

Knowing which family a new vendor belongs to is what makes the eleventh
integration take twenty minutes instead of an afternoon. That classification
is the actual asset here, not any single adapter.

Everything is standard library. No dependencies, no API keys, no accounts.
"""

from __future__ import annotations

import gzip
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict
from typing import Any, Callable

DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
MOBILE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
)


class FetchError(RuntimeError):
    """The posting could not be retrieved and something may be wrong with us.

    Deliberately distinct from an empty result. A shell page that returned
    HTTP 200 is a different failure from a closed req, and conflating them is
    how you end up trusting an empty JD.
    """

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status


class PostingClosed(FetchError):
    """The req is gone, and nothing is wrong with the adapter.

    Worth its own type. Roughly a fifth of any saved pipeline is closed reqs,
    and if a closed posting raises the same error as a broken integration you
    will spend an afternoon debugging a working adapter. 404 and 410 mean the
    vendor is answering correctly; 410 in particular is the vendor explicitly
    telling you it existed and does not now.
    """


@dataclass
class JobPosting:
    """One normalised posting.

    `raw` is kept on purpose. Every adapter loses something in normalisation,
    and the field you did not think to map is always the one you need later.
    """

    source_platform: str
    source_url: str
    title: str | None = None
    company: str | None = None
    locations: list[str] = field(default_factory=list)
    description: str | None = None
    req_id: str | None = None
    department: str | None = None
    employment_type: str | None = None
    posted_at: str | None = None
    comp_min: int | None = None
    comp_max: int | None = None
    comp_currency: str | None = None
    apply_url: str | None = None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    # Fields a caller can reasonably expect. Anything absent is reported
    # rather than defaulted, same argument as the lead qualifier: a missing
    # value is not a zero.
    EXPECTED = (
        "title", "company", "locations", "description",
        "req_id", "posted_at", "comp_min",
    )

    @property
    def unresolved_fields(self) -> list[str]:
        out = []
        for name in self.EXPECTED:
            value = getattr(self, name)
            if value in (None, "", []):
                out.append(name)
        return out

    @property
    def has_description(self) -> bool:
        """A JD under 400 characters is almost always a shell page, not a JD."""
        return bool(self.description) and len(self.description) >= 400

    def to_dict(self) -> dict:
        data = asdict(self)
        data.pop("raw", None)
        data["unresolved_fields"] = self.unresolved_fields
        data["has_description"] = self.has_description
        return data


def http(
    url: str,
    *,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
    mobile: bool = False,
    timeout: int = 25,
) -> str:
    """One HTTP call, returned as text.

    Handles the three things that otherwise bite you on these hosts: gzip
    responses, hosts that reject a default python user agent, and certificate
    chains that some corporate careers subdomains serve incompletely.
    """
    request_headers = {
        "User-Agent": MOBILE_UA if mobile else DESKTOP_UA,
        "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip",
    }
    if headers:
        request_headers.update(headers)

    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    context = ssl.create_default_context()

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=context) as resp:
            body = resp.read()
            if resp.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            return body.decode("utf-8", errors="replace")
    except urllib.error.HTTPError as err:
        if err.code in (404, 410):
            raise PostingClosed(
                f"HTTP {err.code} from {url}: the req is closed or was removed",
                status=err.code,
            ) from err
        raise FetchError(f"HTTP {err.code} from {url}", status=err.code) from err
    except Exception as err:                      # noqa: BLE001
        raise FetchError(f"{type(err).__name__} from {url}: {err}") from err


def http_json(url: str, **kwargs) -> Any:
    text = http(url, **kwargs)
    try:
        return json.loads(text)
    except json.JSONDecodeError as err:
        # The single most common false positive on these hosts: a 200 that
        # returns the SPA shell instead of data. Say so plainly.
        head = text.lstrip()[:60].replace("\n", " ")
        raise FetchError(
            f"expected JSON from {url} but got {len(text)} chars starting {head!r}. "
            "Usually means the endpoint is wrong and the host served its shell page."
        ) from err


TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"[ \t\r\f\v]+")
NL_RE = re.compile(r"\n\s*\n+")

_ENTITIES = {
    "&nbsp;": " ", "&amp;": "and", "&lt;": "<", "&gt;": ">", "&quot;": '"',
    "&#39;": "'", "&apos;": "'", "&mdash;": ", ", "&ndash;": ", ", "&rsquo;": "'",
    "&lsquo;": "'", "&ldquo;": '"', "&rdquo;": '"', "&hellip;": "...",
}


def html_to_text(html: str | None) -> str | None:
    """Strip markup to readable text, preserving block breaks."""
    if not html:
        return None
    text = html
    for entity, replacement in _ENTITIES.items():
        text = text.replace(entity, replacement)
    text = re.sub(r"<\s*(br|/p|/div|/li|/h[1-6]|/tr)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<\s*li[^>]*>", "\n- ", text, flags=re.I)
    text = TAG_RE.sub(" ", text)
    text = WS_RE.sub(" ", text)
    text = NL_RE.sub("\n\n", text)
    return "\n".join(line.strip() for line in text.split("\n")).strip()


MONEY_RE = re.compile(
    r"\$\s?(\d{2,3}(?:,\d{3})|\d{2,3}(?:\.\d)?\s?[kK])(?!\d)"
)


def parse_comp(text: str | None) -> tuple[int | None, int | None]:
    """Pull a salary range out of prose.

    Several vendors carry the band only in the description body even when the
    API exposes a compensation object, so this is a fallback rather than the
    primary path. Returns the lowest and highest plausible annual figures
    found, or (None, None). Deliberately conservative: it would rather find
    nothing than invent a band.
    """
    if not text:
        return None, None

    values: list[int] = []
    for match in MONEY_RE.finditer(text):
        token = match.group(1).replace(",", "").strip()
        if token[-1] in "kK":
            amount = int(float(token[:-1]) * 1000)
        else:
            amount = int(token)
        if 30_000 <= amount <= 900_000:
            values.append(amount)

    if not values:
        return None, None
    if len(values) == 1:
        return values[0], None
    return min(values), max(values)


# ---------------------------------------------------------------- registry

Matcher = Callable[[str], bool]
Fetcher = Callable[[str], JobPosting]

_REGISTRY: list[tuple[str, str, Matcher, Fetcher]] = []


def register(platform: str, family: str, matcher: Matcher):
    """Decorator registering an adapter under its platform and family."""

    def wrap(fetcher: Fetcher) -> Fetcher:
        _REGISTRY.append((platform, family, matcher, fetcher))
        return fetcher

    return wrap


def detect(url: str) -> tuple[str, str] | None:
    """Return (platform, family) for a posting URL, or None if unrecognised."""
    for platform, family, matcher, _ in _REGISTRY:
        if matcher(url):
            return platform, family
    return None


def fetch(url: str) -> JobPosting:
    """Fetch a posting from any recognised ATS."""
    for platform, _family, matcher, fetcher in _REGISTRY:
        if matcher(url):
            return fetcher(url)
    raise FetchError(
        f"no adapter matches {url}. Registered platforms: "
        + ", ".join(sorted({p for p, _, _, _ in _REGISTRY}))
    )


def registry() -> list[dict[str, str]]:
    return [
        {"platform": p, "family": f}
        for p, f, _, _ in sorted(_REGISTRY, key=lambda r: (r[1], r[0]))
    ]
