"""FAMILY 1: public JSON board APIs.

The easy family. Each vendor exposes a board endpoint that returns real JSON
with no key and no session. The work is entirely in knowing the endpoint and
in pulling the org slug and req id back out of a posting URL, which every
vendor shapes differently.

Worth noting what this family gives you for free: request the board without a
req id and you get *every* open role at that company. That turns a single
posting into a whole-company scan at no extra cost.
"""

from __future__ import annotations

import re

from .base import (
    JobPosting,
    FetchError,
    html_to_text,
    http_json,
    parse_comp,
    register,
)

FAMILY = "1-json-board"


# ------------------------------------------------------------- greenhouse

GH_URL_PATTERNS = [
    re.compile(r"(?:job-)?boards\.greenhouse\.io/(?P<slug>[^/?#]+)/jobs/(?P<id>\d+)"),
    re.compile(r"greenhouse\.io/embed/job_app\?for=(?P<slug>[^&]+)&token=(?P<id>\d+)"),
    re.compile(r"greenhouse\.io/embed/job_app\?token=(?P<id>\d+)&for=(?P<slug>[^&]+)"),
]


@register("greenhouse", FAMILY, lambda u: "greenhouse.io" in u)
def greenhouse(url: str) -> JobPosting:
    for pattern in GH_URL_PATTERNS:
        m = pattern.search(url)
        if m:
            slug, req = m.group("slug"), m.group("id")
            break
    else:
        raise FetchError(f"could not read a greenhouse slug and job id from {url}")

    data = http_json(
        f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{req}"
    )
    text = html_to_text(data.get("content"))
    low, high = parse_comp(text)

    return JobPosting(
        source_platform="greenhouse",
        source_url=url,
        title=(data.get("title") or "").strip() or None,
        company=slug,
        locations=[data["location"]["name"]] if data.get("location") else [],
        description=text,
        req_id=str(data.get("id")) if data.get("id") else None,
        department=", ".join(d.get("name", "") for d in data.get("departments") or []) or None,
        posted_at=data.get("updated_at"),
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        apply_url=data.get("absolute_url"),
        raw=data,
    )


def greenhouse_board(slug: str) -> list[dict]:
    """Every open req at a company. The free sibling scan."""
    return http_json(f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs").get("jobs", [])


# ------------------------------------------------------------------ ashby

ASHBY_URL = re.compile(r"ashbyhq\.com/(?P<slug>[^/?#]+)/(?P<id>[0-9a-f-]{16,})")


@register("ashby", FAMILY, lambda u: "ashbyhq.com" in u)
def ashby(url: str) -> JobPosting:
    m = ASHBY_URL.search(url)
    if not m:
        raise FetchError(f"could not read an ashby org and posting id from {url}")
    slug, req = m.group("slug"), m.group("id")

    # Ashby has no single-posting endpoint. The board returns everything, so
    # fetch once and select. Cheap, and it means a miss is provably a miss.
    board = http_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    )
    jobs = board.get("jobs", [])
    match = next((j for j in jobs if j.get("id") == req), None)
    if match is None:
        raise FetchError(
            f"posting {req} is not on the {slug} board ({len(jobs)} open reqs). "
            "Usually means the req closed."
        )

    text = html_to_text(match.get("descriptionHtml"))
    comp = match.get("compensation") or {}
    low, high = parse_comp(comp.get("compensationTierSummary"))
    if low is None:
        # Several Ashby tenants leave the compensation object empty and put
        # the band in the body instead. Verified on Planhat, 2026-09-10.
        low, high = parse_comp(text)

    return JobPosting(
        source_platform="ashby",
        source_url=url,
        title=(match.get("title") or "").strip() or None,
        company=slug,
        locations=[match["location"]] if match.get("location") else [],
        description=text,
        req_id=match.get("id"),
        department=match.get("department") or match.get("team"),
        employment_type=match.get("employmentType"),
        posted_at=match.get("publishedAt"),
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        apply_url=match.get("applyUrl") or match.get("jobUrl"),
        raw=match,
    )


def ashby_board(slug: str) -> list[dict]:
    return http_json(
        f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    ).get("jobs", [])


# ------------------------------------------------------------------ lever

LEVER_URL = re.compile(r"lever\.co/(?P<slug>[^/?#]+)/(?P<id>[0-9a-f-]{16,})")


@register("lever", FAMILY, lambda u: "lever.co" in u)
def lever(url: str) -> JobPosting:
    m = LEVER_URL.search(url)
    if not m:
        raise FetchError(f"could not read a lever org and posting id from {url}")
    slug, req = m.group("slug"), m.group("id")

    data = http_json(f"https://api.lever.co/v0/postings/{slug}/{req}")
    text = html_to_text(data.get("description", "") + " " + (data.get("descriptionBody") or ""))
    for section in data.get("lists") or []:
        text = (text or "") + "\n\n" + (section.get("text") or "") + "\n" + html_to_text(section.get("content")) or ""

    categories = data.get("categories") or {}
    low, high = parse_comp(text)

    return JobPosting(
        source_platform="lever",
        source_url=url,
        title=data.get("text"),
        company=slug,
        locations=[categories["location"]] if categories.get("location") else [],
        description=text,
        req_id=data.get("id"),
        department=categories.get("department") or categories.get("team"),
        employment_type=categories.get("commitment"),
        posted_at=str(data.get("createdAt")) if data.get("createdAt") else None,
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        apply_url=data.get("applyUrl") or data.get("hostedUrl"),
        raw=data,
    )


def lever_board(slug: str) -> list[dict]:
    return http_json(f"https://api.lever.co/v0/postings/{slug}?mode=json")


# -------------------------------------------------------- smartrecruiters

SR_URL = re.compile(r"smartrecruiters\.com/(?P<slug>[^/?#]+)/(?P<id>\d{6,})")


@register("smartrecruiters", FAMILY, lambda u: "smartrecruiters.com" in u)
def smartrecruiters(url: str) -> JobPosting:
    m = SR_URL.search(url)
    if not m:
        raise FetchError(f"could not read a smartrecruiters company and id from {url}")
    slug, req = m.group("slug"), m.group("id")

    data = http_json(f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{req}")
    ad = data.get("jobAd", {}).get("sections", {})
    parts = [
        ad.get("companyDescription", {}).get("text"),
        ad.get("jobDescription", {}).get("text"),
        ad.get("qualifications", {}).get("text"),
        ad.get("additionalInformation", {}).get("text"),
    ]
    text = html_to_text("\n\n".join(p for p in parts if p))
    location = data.get("location") or {}
    where = ", ".join(
        str(location[k]) for k in ("city", "region", "country") if location.get(k)
    )
    low, high = parse_comp(text)

    return JobPosting(
        source_platform="smartrecruiters",
        source_url=url,
        title=data.get("name"),
        company=data.get("company", {}).get("name") or slug,
        locations=[where] if where else [],
        description=text,
        req_id=str(data.get("refNumber") or data.get("id") or req),
        department=(data.get("department") or {}).get("label"),
        employment_type=(data.get("typeOfEmployment") or {}).get("label"),
        posted_at=data.get("releasedDate"),
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        apply_url=data.get("applyUrl"),
        raw=data,
    )


def smartrecruiters_board(slug: str) -> list[dict]:
    return http_json(
        f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100"
    ).get("content", [])


# --------------------------------------------------------------- workable

WORKABLE_URL = re.compile(r"workable\.com/(?:[a-z]{2}/)?(?P<slug>[^/?#]+)/j/(?P<id>[0-9A-F]{6,})", re.I)


@register("workable", FAMILY, lambda u: "workable.com" in u)
def workable(url: str) -> JobPosting:
    m = WORKABLE_URL.search(url)
    if not m:
        raise FetchError(f"could not read a workable account and shortcode from {url}")
    slug, req = m.group("slug"), m.group("id").upper()

    board = http_json(
        f"https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true"
    )
    jobs = board.get("jobs", [])
    match = next((j for j in jobs if (j.get("shortcode") or "").upper() == req), None)
    if match is None:
        raise FetchError(
            f"shortcode {req} is not on the {slug} board ({len(jobs)} open reqs)"
        )

    text = html_to_text(
        (match.get("description") or "") + "\n\n" + (match.get("requirements") or "")
    )
    low, high = parse_comp(text)

    return JobPosting(
        source_platform="workable",
        source_url=url,
        title=match.get("title"),
        company=board.get("name") or slug,
        locations=[match.get("location", {}).get("location_str")]
        if match.get("location") else [],
        description=text,
        req_id=match.get("shortcode"),
        department=match.get("department"),
        employment_type=match.get("employment_type"),
        posted_at=match.get("published_on") or match.get("created_at"),
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        apply_url=match.get("application_url") or match.get("url"),
        raw=match,
    )


# --------------------------------------------------------------- rippling

RIPPLING_URL = re.compile(
    r"rippling(?:ats)?\.com/(?:[^/]+/)?(?P<slug>[^/?#]+)/jobs/(?P<id>[0-9a-f-]{16,})"
)


@register("rippling", FAMILY, lambda u: "rippling.com" in u)
def rippling(url: str) -> JobPosting:
    m = RIPPLING_URL.search(url)
    if not m:
        raise FetchError(f"could not read a rippling board slug and uuid from {url}")
    slug, req = m.group("slug"), m.group("id")

    data = http_json(
        f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{req}"
    )
    # description arrives as a dict of HTML fragments rather than one string
    raw_desc = data.get("description")
    if isinstance(raw_desc, dict):
        text = html_to_text("\n\n".join(str(v) for v in raw_desc.values() if v))
    else:
        text = html_to_text(raw_desc)
    low, high = parse_comp(text)

    return JobPosting(
        source_platform="rippling",
        source_url=url,
        title=data.get("name") or data.get("title"),
        company=slug,
        locations=[data.get("workLocation", {}).get("label")]
        if isinstance(data.get("workLocation"), dict) else [],
        description=text,
        req_id=data.get("id") or req,
        department=(data.get("department") or {}).get("label")
        if isinstance(data.get("department"), dict) else data.get("department"),
        employment_type=data.get("employmentType"),
        posted_at=data.get("createdAt"),
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        raw=data,
    )


def rippling_board(slug: str) -> list[dict]:
    """Drop the uuid and the same endpoint lists every open req."""
    data = http_json(f"https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs")
    return data if isinstance(data, list) else data.get("items", [])
