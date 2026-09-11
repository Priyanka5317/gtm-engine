"""FAMILY 3: undocumented internal endpoints.

The hard family, and the one worth writing down. Each of these vendors serves
a client rendered page, so fetching the posting URL returns a shell with no
job description in it. Worse, the shell returns HTTP 200, so a naive scraper
records a success and stores an empty JD.

Each adapter below needs one specific thing to answer: an exact internal
path, a specific header, a different HTTP method, or a query flag. None of it
is documented. All of it was found by watching what the page itself requests.

The reusable lesson is the failure mode, not the endpoints: on this family a
200 means nothing. Assert you got a real job description, or you will fill a
database with confident blanks.
"""

from __future__ import annotations

import json
import re
import urllib.parse

from .base import (
    JobPosting,
    FetchError,
    html_to_text,
    http,
    http_json,
    parse_comp,
    register,
)

FAMILY = "3-internal-api"


def _jsonld(html: str) -> dict | None:
    """Pull the first JobPosting JSON-LD block out of a page."""
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        html, re.S | re.I,
    ):
        try:
            blob = json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            continue
        candidates = blob if isinstance(blob, list) else [blob]
        for item in candidates:
            if isinstance(item, dict) and "JobPosting" in str(item.get("@type", "")):
                return item
    return None


def _from_jsonld(item: dict, platform: str, url: str) -> JobPosting:
    salary = item.get("baseSalary") or {}
    value = salary.get("value") or {}
    low = value.get("minValue")
    high = value.get("maxValue")
    text = html_to_text(item.get("description"))
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

    return JobPosting(
        source_platform=platform,
        source_url=url,
        title=item.get("title"),
        company=(item.get("hiringOrganization") or {}).get("name"),
        locations=locations,
        description=text,
        req_id=str(item.get("identifier", {}).get("value"))
        if isinstance(item.get("identifier"), dict) else item.get("identifier"),
        employment_type=item.get("employmentType"),
        posted_at=item.get("datePosted"),
        comp_min=int(low) if isinstance(low, (int, float)) else None,
        comp_max=int(high) if isinstance(high, (int, float)) else None,
        comp_currency=salary.get("currency"),
        raw=item,
    )


# ----------------------------------------------------------------- workday

WORKDAY_URL = re.compile(
    r"https?://(?P<tenant>[^.]+)\.(?P<wd>wd\d+)\.myworkdayjobs\.com/"
    r"(?:[a-z]{2}-[A-Z]{2}/)?(?P<site>[^/]+)/job/(?P<path>.+?)/?$"
)


@register("workday", FAMILY, lambda u: "myworkdayjobs.com" in u)
def workday(url: str) -> JobPosting:
    """The CXS endpoint needs the WHOLE path after /job/, not the bare req id.

    Passing just the trailing requisition number returns a 404, which is the
    mistake that makes people conclude Workday is unscrapable.
    """
    m = WORKDAY_URL.match(url.split("?")[0])
    if not m:
        raise FetchError(f"could not parse a workday tenant, site and job path from {url}")

    tenant, wd, site, path = m.group("tenant"), m.group("wd"), m.group("site"), m.group("path")
    endpoint = (
        f"https://{tenant}.{wd}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{site}/job/{path}"
    )
    data = http_json(endpoint, headers={"Accept": "application/json"})
    info = data.get("jobPostingInfo") or {}

    text = html_to_text(info.get("jobDescription"))
    low, high = parse_comp(text)

    locations = [info["location"]] if info.get("location") else []
    for extra in info.get("additionalLocations") or []:
        locations.append(extra)

    return JobPosting(
        source_platform="workday",
        source_url=url,
        title=info.get("title"),
        company=tenant,
        locations=locations,
        description=text,
        req_id=info.get("jobReqId"),
        employment_type=info.get("timeType"),
        posted_at=info.get("startDate") or info.get("postedOn"),
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        apply_url=info.get("externalUrl"),
        raw=data,
    )


# ------------------------------------------------------------------- icims

@register("icims", FAMILY, lambda u: "icims.com" in u)
def icims(url: str) -> JobPosting:
    """Desktop returns an empty shell. Mobile flags plus a mobile UA return the JD.

    Session tokens (`eem=`, `code=`) in a shared link must be stripped or the
    request is rejected as a replayed session.
    """
    clean = urllib.parse.urlsplit(url)
    keep = {
        k: v for k, v in urllib.parse.parse_qsl(clean.query)
        if k.lower() not in ("eem", "code", "csrf", "hashed")
    }
    keep["mobile"] = "true"
    keep["needsRedirect"] = "false"
    target = urllib.parse.urlunsplit(
        (clean.scheme, clean.netloc, clean.path, urllib.parse.urlencode(keep), "")
    )

    html = http(target, mobile=True)
    item = _jsonld(html)
    if item:
        posting = _from_jsonld(item, "icims", url)
        posting.raw = {"note": "recovered from JSON-LD on the mobile view"}
        return posting

    # Fall back to the rendered mobile body, which is still real text.
    text = html_to_text(html)
    if not text or len(text) < 400:
        raise FetchError(
            f"icims returned {len(text or '')} chars of text for {url}. "
            "The mobile flags were applied, so this is likely a closed req."
        )
    title = re.search(r"<title>(.*?)</title>", html, re.S | re.I)
    low, high = parse_comp(text)
    return JobPosting(
        source_platform="icims",
        source_url=url,
        title=html_to_text(title.group(1)) if title else None,
        description=text,
        comp_min=low,
        comp_max=high,
        raw={"note": "recovered from the rendered mobile body, no JSON-LD present"},
    )


# ------------------------------------------------------------------ oracle

ORACLE_URL = re.compile(
    r"https?://(?P<host>[^/]+oraclecloud\.com)/hcmUI/CandidateExperience/"
    r"(?P<lang>[^/]+)/sites/(?P<site>[^/]+)/job/(?P<id>\d+)"
)


@register("oracle", FAMILY, lambda u: "oraclecloud.com" in u and "CandidateExperience" in u)
def oracle(url: str) -> JobPosting:
    """Oracle Recruiting Cloud.

    The documented `ByRequisitionId` finder returns 400. What works is the
    detail resource with the id as a PATH parameter, against the pod host
    rather than any vanity careers domain.
    """
    m = ORACLE_URL.search(url)
    if not m:
        raise FetchError(f"could not parse an oracle pod host and job id from {url}")
    host, site, req = m.group("host"), m.group("site"), m.group("id")

    endpoint = (
        f"https://{host}/hcmRestApi/resources/latest/"
        f"recruitingCEJobRequisitionDetails/{req}"
        f"?expand=all&onlyData=true"
    )
    try:
        data = http_json(endpoint, headers={"Accept": "application/json"})
    except FetchError:
        # Some pods only answer the collection form with a site filter.
        endpoint = (
            f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
            f"?onlyData=true&expand=all"
            f"&finder=ByRequisitionId;requisitionId={req},siteNumber={site}"
        )
        data = http_json(endpoint, headers={"Accept": "application/json"})

    items = data.get("items") or [data]
    info = items[0] if items else {}
    text = html_to_text(info.get("ExternalDescriptionStr") or info.get("ShortDescriptionStr"))
    low, high = parse_comp(text)

    locations = []
    for loc in info.get("requisitionLocations") or []:
        where = ", ".join(
            str(loc[k]) for k in ("TownOrCity", "Region2", "CountryCode") if loc.get(k)
        )
        if where:
            locations.append(where)
    if not locations and info.get("PrimaryLocation"):
        locations = [info["PrimaryLocation"]]

    return JobPosting(
        source_platform="oracle",
        source_url=url,
        title=info.get("Title"),
        company=info.get("CustomerName") or host.split(".")[0],
        locations=locations,
        description=text,
        req_id=str(info.get("Id") or req),
        posted_at=info.get("PostedDate"),
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        raw=info,
    )


# --------------------------------------------------------------- eightfold

# Two URL shapes in the wild, and the path form is the common one. A share
# link reads /careers/job/{pid}; only a link copied mid-session carries
# ?pid={pid}. Matching just the query form failed on every real posting seen
# in a 21,000 row feed, which is how this was caught.
EIGHTFOLD_TENANT = re.compile(
    r"https?://(?P<tenant>[^.]+)\.eightfold\.ai/careers"
    r"(?:/job/(?P<pid_path>\d+)|.*?[?&]pid=(?P<pid_query>\d+))"
)


@register("eightfold", FAMILY, lambda u: "eightfold.ai" in u)
def eightfold(url: str) -> JobPosting:
    """Tenant hosts expose an apply API. Custom domains give only JSON-LD.

    The ATS `department` tag on this vendor has been observed to be flatly
    wrong, so it is carried through but should not be trusted for routing.
    """
    m = EIGHTFOLD_TENANT.search(url)
    if not m:
        raise FetchError(
            f"could not parse an eightfold tenant and pid from {url}. "
            "Custom careers domains carry no pid; read their JSON-LD instead."
        )
    tenant = m.group("tenant")
    pid = m.group("pid_path") or m.group("pid_query")
    # The API wants the employer's own domain. A share link has no domain
    # param, and the tenant slug is the company, so fall back to it rather
    # than sending an empty value the API answers inconsistently.
    domain = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("domain", [""])[0]
    if not domain:
        domain = f"{tenant}.com"

    data = http_json(
        f"https://{tenant}.eightfold.ai/api/apply/v2/jobs/{pid}"
        f"?domain={domain}&microsite=1&pid={pid}"
    )
    text = html_to_text(data.get("job_description") or data.get("descriptionHtml"))
    low, high = parse_comp(text)

    return JobPosting(
        source_platform="eightfold",
        source_url=url,
        title=data.get("name") or data.get("title"),
        company=domain or tenant,
        locations=data.get("locations") or ([data["location"]] if data.get("location") else []),
        description=text,
        req_id=str(data.get("display_job_id") or data.get("id") or pid),
        department=data.get("department"),
        posted_at=data.get("create_date") or data.get("t_create"),
        comp_min=low,
        comp_max=high,
        comp_currency="USD" if low else None,
        raw=data,
    )
