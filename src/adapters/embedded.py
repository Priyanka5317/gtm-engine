"""FAMILY 2: JSON embedded in the served HTML.

The middle family. There is no API to find, but there is no rendering to do
either. The server ships the whole posting inside a script tag and the client
framework hydrates from it, so the data is already in the response you have.

Three shapes cover almost everything:

  __NEXT_DATA__       Next.js hydration payload  (Dayforce)
  JSON-LD JobPosting  schema.org block for SEO   (Phenom, custom domains)
  urlencoded state    framework session blob     (Taleo)

The tell for this family is a page that looks empty to a text extractor but
weighs 200KB. If the bytes are there and the text is not, stop looking for an
endpoint and read the script tags.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from .base import (
    JobPosting,
    FetchError,
    PostingClosed,
    html_to_text,
    http,
    parse_comp,
    register,
)

FAMILY = "2-embedded-json"


def _next_data(html: str) -> dict:
    m = re.search(
        r'<script[^>]+id=["\']__NEXT_DATA__["\'][^>]*>(.*?)</script>', html, re.S
    )
    if not m:
        raise FetchError("no __NEXT_DATA__ block in the page")
    return json.loads(m.group(1))


def _jsonld_posting(html: str) -> dict | None:
    for m in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        try:
            blob = json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            continue
        for item in (blob if isinstance(blob, list) else [blob]):
            if isinstance(item, dict) and "JobPosting" in str(item.get("@type", "")):
                return item
    return None


# ---------------------------------------------------------------- dayforce

@register("dayforce", FAMILY, lambda u: "dayforcehcm.com" in u)
def dayforce(url: str) -> JobPosting:
    """WebFetch style extraction gets a nav shell. The payload is in __NEXT_DATA__.

    Two traps. The compensation band lives ONLY inside
    `jobPostingAttributes[]`, not on the job object. And the id in the URL is
    the POSTING id, which is not the requisition id, so matching on it against
    an internal tracker silently fails.
    """
    html = http(url)
    data = _next_data(html)
    job = data.get("props", {}).get("pageProps", {}).get("jobData")
    if not job:
        raise PostingClosed(f"no jobData in the hydration payload for {url}")

    # Field names verified live against two tenants, 2026-09-10. The
    # description is NESTED under jobPostingContent, which is why reading
    # job["description"] returns a posting with a title and no body: a
    # confident blank, the exact failure this family is prone to.
    content = job.get("jobPostingContent") or {}
    text = html_to_text(
        (content.get("jobDescriptionHeader") or "")
        + "<br><br>"
        + (content.get("jobDescription") or "")
    )

    low = high = None
    for attr in job.get("jobPostingAttributes") or []:
        label = str(attr.get("name") or "").lower()
        if any(k in label for k in ("salary", "compensation", "pay", "rate")):
            low, high = parse_comp(str(attr.get("value")))
            if low:
                break
    if low is None:
        low, high = parse_comp(text)

    locations = []
    for loc in job.get("postingLocations") or []:
        if isinstance(loc, dict):
            where = ", ".join(
                str(loc[k]) for k in
                ("city", "state", "stateProvince", "country", "address")
                if loc.get(k)
            )
            if where:
                locations.append(where)
        elif loc:
            locations.append(str(loc))
    if not locations and job.get("hasVirtualLocation"):
        locations = ["Remote"]

    department = next(
        (a.get("value") for a in job.get("jobPostingAttributes") or []
         if str(a.get("name", "")).lower() in ("jobfunction", "department")),
        None,
    )

    return JobPosting(
        source_platform="dayforce",
        source_url=url,
        title=(job.get("jobTitle") or "").strip() or None,
        locations=locations,
        description=text,
        # jobReqId, NOT the jobPostingId in the URL. Verified different on a
        # live posting: URL id 62529, requisition id 9004. Matching an
        # internal tracker on the URL id fails silently.
        req_id=str(job["jobReqId"]) if job.get("jobReqId") else None,
        department=department,
        posted_at=job.get("postingStartTimestampUTC"),
        comp_min=low,
        comp_max=high,
        comp_currency=job.get("isoCurrencyRegion") if low else None,
        raw=job,
    )


# ------------------------------------------------------------------- taleo

# Taleo's own wording for an expired req, served with HTTP 200.
TALEO_CLOSED = re.compile(
    r"job description you are trying to view is\s*(?:</?[^>]+>\s*)*no longer available"
    r"|requisition (?:is )?no longer available",
    re.I | re.S,
)


@register("taleo", FAMILY, lambda u: "taleo.net" in u or "masscareers" in u.lower())
def taleo(url: str) -> JobPosting:
    """Taleo hides the posting in a urlencoded `initialHistory` parameter.

    A plain fetch returns an EMPTY template, which is a false negative rather
    than a closed req. The REST API on these tenants is dead (searchjobs
    returns HTTP 500), so its 404s prove nothing about whether a job exists.

    An expired req is a third case, and it is the one that used to be
    misreported. Taleo serves it as HTTP 200 carrying a "no longer available"
    string, so neither the status code nor the missing blob distinguishes
    "gone" from "we failed to parse it" - only the body does. Assert on the
    body, or every expired posting reads as a broken adapter.
    """
    html = http(url)

    if TALEO_CLOSED.search(html):
        raise PostingClosed(
            "Taleo states the job description is no longer available. Served as "
            "HTTP 200, so only the body distinguishes this from an extraction failure.",
            status=200,
        )

    item = _jsonld_posting(html)
    if item:
        text = html_to_text(item.get("description"))
        low, high = parse_comp(text)
        return JobPosting(
            source_platform="taleo",
            source_url=url,
            title=item.get("title"),
            company=(item.get("hiringOrganization") or {}).get("name"),
            description=text,
            posted_at=item.get("datePosted"),
            comp_min=low,
            comp_max=high,
            raw={"note": "recovered from JSON-LD"},
        )

    m = re.search(r"initialHistory\s*[:=]\s*['\"]([^'\"]+)['\"]", html)
    if not m:
        raise FetchError(
            "no initialHistory blob and no JSON-LD in the Taleo page. "
            "A plain fetch of this vendor returns an empty template, so treat "
            "this as an extraction failure rather than a closed req."
        )
    decoded = urllib.parse.unquote(urllib.parse.unquote(m.group(1)))
    text = html_to_text(decoded)
    if not text or len(text) < 400:
        raise FetchError(f"initialHistory decoded to only {len(text or '')} chars")

    low, high = parse_comp(text)
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    return JobPosting(
        source_platform="taleo",
        source_url=url,
        title=html_to_text(title.group(1)) if title else None,
        description=text,
        comp_min=low,
        comp_max=high,
        raw={"note": "recovered by urldecoding initialHistory twice"},
    )


# ------------------------------------------------------------------ phenom

@register(
    "phenom", FAMILY,
    lambda u: bool(re.search(r"/(?:careers?|jobs?)/(?:job/)?\d{5,}", u))
    and not any(v in u for v in (
        "greenhouse.io", "ashbyhq.com", "lever.co", "myworkdayjobs.com",
        "icims.com", "oraclecloud.com", "smartrecruiters.com", "workable.com",
        "rippling.com", "dayforcehcm.com", "taleo.net", "eightfold.ai",
    )),
)
def phenom(url: str) -> JobPosting:
    """Phenom People and most enterprise custom careers domains.

    No usable API, but they invest heavily in SEO, which means a complete and
    well formed JSON-LD JobPosting block is almost always present. The SEO
    incentive is what makes this family reliable: they WANT it machine
    readable.
    """
    html = http(url)
    item = _jsonld_posting(html)
    if not item:
        raise FetchError(
            f"no JSON-LD JobPosting block at {url}. "
            "If the page is heavy but text-empty it is still family 2, so "
            "check for a hydration payload under a different script id."
        )

    salary = item.get("baseSalary") or {}
    value = salary.get("value") or {}
    text = html_to_text(item.get("description"))
    low, high = value.get("minValue"), value.get("maxValue")
    if low is None:
        low, high = parse_comp(text)

    places = item.get("jobLocation")
    places = places if isinstance(places, list) else [places] if places else []
    locations = []
    for place in places:
        addr = (place or {}).get("address") or {}
        where = ", ".join(
            str(addr[k]) for k in ("addressLocality", "addressRegion", "addressCountry")
            if addr.get(k)
        )
        if where:
            locations.append(where)

    identifier = item.get("identifier")
    return JobPosting(
        source_platform="phenom",
        source_url=url,
        title=item.get("title"),
        company=(item.get("hiringOrganization") or {}).get("name"),
        locations=locations,
        description=text,
        req_id=str(identifier.get("value")) if isinstance(identifier, dict) else identifier,
        employment_type=item.get("employmentType"),
        posted_at=item.get("datePosted"),
        comp_min=int(low) if isinstance(low, (int, float)) else None,
        comp_max=int(high) if isinstance(high, (int, float)) else None,
        comp_currency=salary.get("currency"),
        raw={"note": "recovered from JSON-LD JobPosting"},
    )
