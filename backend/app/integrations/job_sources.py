"""Permitted job-source adapters. Every result is a real posting from the provider's API.

Free, keyless public APIs: Remotive, Arbeitnow, RemoteOK (attribution + link-back required,
shown in the UI), Greenhouse and Lever public boards (user supplies board/site ids).
Keyed providers: Adzuna, SerpApi Google Jobs (which can surface LinkedIn/Naukri source links).
No LinkedIn/Naukri scraping is performed.
"""
from __future__ import annotations

import asyncio
import html
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

from .safe_fetch import USER_AGENT, html_to_text

PROVIDER_INFO: dict[str, dict[str, Any]] = {
    "remotive": {"label": "Remotive", "keyless": True, "attribution": "Jobs via Remotive (remotive.com) — link back required",
                 "terms": "https://remotive.com/api-documentation", "min_interval_s": 6 * 3600},
    "arbeitnow": {"label": "Arbeitnow", "keyless": True, "attribution": "Jobs via Arbeitnow (arbeitnow.com)", "terms": "https://www.arbeitnow.com/api"},
    "remoteok": {"label": "Remote OK", "keyless": True, "attribution": "Jobs via Remote OK (remoteok.com) — link back required", "terms": "https://remoteok.com/api"},
    "greenhouse": {"label": "Greenhouse boards", "keyless": True, "needs": "board tokens (e.g. stripe, airbnb)", "terms": "https://developers.greenhouse.io/job-board.html"},
    "lever": {"label": "Lever boards", "keyless": True, "needs": "site ids (e.g. company names in jobs.lever.co/<site>)", "terms": "https://github.com/lever/postings-api"},
    "adzuna": {"label": "Adzuna", "keyless": False, "needs": "ADZUNA_APP_ID + ADZUNA_APP_KEY", "terms": "https://developer.adzuna.com/"},
    "serpapi": {"label": "SerpApi Google Jobs", "keyless": False, "needs": "SERPAPI_API_KEY", "terms": "https://serpapi.com/google-jobs-api"},
}

_cache: dict[str, tuple[float, Any]] = {}


@dataclass
class JobListing:
    provider: str
    external_id: str
    title: str
    company: str | None
    location: str | None
    url: str
    description: str
    posted_at: str | None = None
    employment_type: str | None = None
    remote: bool | None = None
    tags: list[str] = field(default_factory=list)
    description_is_partial: bool = False
    platform: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchFilters:
    query: str
    location: str | None = None
    level: str | None = None  # internship | entry | any
    remote_only: bool = False
    greenhouse_boards: list[str] = field(default_factory=list)
    lever_sites: list[str] = field(default_factory=list)
    limit_per_provider: int = 25


LEVEL_PATTERNS = {
    "internship": re.compile(r"\b(intern(ship)?|co-?op|placement|trainee|working student|werkstudent)\b", re.I),
    "entry": re.compile(r"\b(junior|jr\.?|entry[- ]level|graduate|new grad|associate|early career|intern(ship)?|trainee|fresher)\b", re.I),
}


def platform_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    for key, plat in (("linkedin.", "linkedin"), ("naukri.", "naukri"), ("greenhouse.io", "greenhouse"), ("lever.co", "lever"),
                      ("indeed.", "indeed"), ("glassdoor.", "glassdoor"), ("remotive.", "remotive"), ("arbeitnow.", "arbeitnow"),
                      ("remoteok.", "remoteok"), ("adzuna.", "adzuna"), ("ashbyhq.", "ashby"), ("workable.", "workable"), ("wellfound.", "wellfound")):
        if key in host:
            return plat
    return host or "unknown"


def _iso_from_epoch(v: Any, ms: bool = False) -> str | None:
    try:
        t = float(v) / (1000 if ms else 1)
        return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return None


def _matches(listing: JobListing, f: SearchFilters, client_side_query: bool) -> bool:
    blob = f"{listing.title} {listing.company or ''} {' '.join(listing.tags)} {listing.description[:4000]}".lower()
    if client_side_query and f.query:
        terms = [t for t in re.split(r"\s+", f.query.lower().strip()) if t]
        title_l = listing.title.lower()
        if not all(t in blob for t in terms) or not any(t in title_l or t in " ".join(listing.tags).lower() for t in terms):
            return False
    if f.location and listing.location and f.location.lower() not in (listing.location or "").lower() and not (listing.remote or "remote" in (listing.location or "").lower()):
        return False
    if f.location and not listing.location and not listing.remote:
        return False
    if f.remote_only and not (listing.remote or "remote" in (listing.location or "").lower()):
        return False
    if f.level in LEVEL_PATTERNS and not LEVEL_PATTERNS[f.level].search(f"{listing.title} {listing.employment_type or ''} {' '.join(listing.tags)}"):
        return False
    return True


async def _get_json(client: httpx.AsyncClient, url: str, params: dict | None = None, cache_s: int = 900) -> Any:
    key = f"{url}?{sorted((params or {}).items())}"
    now = time.time()
    if key in _cache and now - _cache[key][0] < cache_s:
        return _cache[key][1]
    r = await client.get(url, params=params)
    if r.status_code == 429:
        raise RuntimeError(f"rate limited (429); retry after {r.headers.get('retry-after', '?')}s")
    if r.status_code in (401, 403):
        raise RuntimeError(f"access denied ({r.status_code}) — check credentials/terms")
    r.raise_for_status()
    data = r.json()
    _cache[key] = (now, data)
    return data


async def search_remotive(c: httpx.AsyncClient, f: SearchFilters) -> list[JobListing]:
    data = await _get_json(c, "https://remotive.com/api/remote-jobs", {"search": f.query, "limit": 100}, cache_s=6 * 3600)
    out = []
    for j in data.get("jobs", []):
        out.append(JobListing("remotive", str(j["id"]), j.get("title", ""), j.get("company_name"), j.get("candidate_required_location") or "Remote",
                              j.get("url", ""), html_to_text(j.get("description") or ""), j.get("publication_date"), j.get("job_type"), True,
                              [str(t) for t in j.get("tags") or []]))
    return [x for x in out if _matches(x, f, client_side_query=False)]


async def search_arbeitnow(c: httpx.AsyncClient, f: SearchFilters) -> list[JobListing]:
    out = []
    for page in (1, 2, 3):
        data = await _get_json(c, "https://www.arbeitnow.com/api/job-board-api", {"page": page}, cache_s=3600)
        for j in data.get("data", []):
            desc = html_to_text(html.unescape(j.get("description") or ""))
            out.append(JobListing("arbeitnow", j.get("slug", ""), j.get("title", ""), j.get("company_name"), j.get("location"), j.get("url", ""),
                                  desc, _iso_from_epoch(j.get("created_at")), ", ".join(j.get("job_types") or []) or None, bool(j.get("remote")),
                                  [str(t) for t in j.get("tags") or []]))
    return [x for x in out if _matches(x, f, client_side_query=True)]


async def search_remoteok(c: httpx.AsyncClient, f: SearchFilters) -> list[JobListing]:
    data = await _get_json(c, "https://remoteok.com/api", cache_s=3600)
    out = []
    for j in data[1:] if isinstance(data, list) else []:
        out.append(JobListing("remoteok", str(j.get("id")), j.get("position", ""), j.get("company"), j.get("location") or "Remote", j.get("url", ""),
                              html_to_text(j.get("description") or ""), j.get("date"), None, True, [str(t) for t in j.get("tags") or []]))
    return [x for x in out if _matches(x, f, client_side_query=True)]


async def search_greenhouse(c: httpx.AsyncClient, f: SearchFilters) -> list[JobListing]:
    out = []
    for board in f.greenhouse_boards[:10]:
        board = re.sub(r"[^a-z0-9_-]", "", board.lower())
        if not board:
            continue
        data = await _get_json(c, f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", {"content": "true"}, cache_s=1800)
        for j in data.get("jobs", []):
            out.append(JobListing("greenhouse", f"{board}:{j['id']}", j.get("title", ""), j.get("company_name") or board,
                                  (j.get("location") or {}).get("name"), j.get("absolute_url", ""), html_to_text(html.unescape(j.get("content") or "")),
                                  j.get("first_published") or j.get("updated_at"), None, None, [d.get("name", "") for d in j.get("departments") or []],
                                  extra={"board": board}))
    return [x for x in out if _matches(x, f, client_side_query=True)]


async def search_lever(c: httpx.AsyncClient, f: SearchFilters) -> list[JobListing]:
    out = []
    for site in f.lever_sites[:10]:
        site = re.sub(r"[^a-z0-9_-]", "", site.lower())
        if not site:
            continue
        data = await _get_json(c, f"https://api.lever.co/v0/postings/{site}", {"mode": "json"}, cache_s=1800)
        for j in data if isinstance(data, list) else []:
            cats = j.get("categories") or {}
            lists = "\n".join(f"{l.get('text', '')}\n{html_to_text(l.get('content', ''))}" for l in j.get("lists") or [])
            desc = "\n\n".join(x for x in (j.get("descriptionPlain") or "", lists, j.get("additionalPlain") or "") if x)
            out.append(JobListing("lever", f"{site}:{j['id']}", j.get("text", ""), site, cats.get("location"), j.get("hostedUrl", ""), desc,
                                  _iso_from_epoch(j.get("createdAt"), ms=True), cats.get("commitment"), (j.get("workplaceType") == "remote") or None,
                                  [cats.get("team") or "", cats.get("department") or ""], extra={"site": site}))
    return [x for x in out if _matches(x, f, client_side_query=True)]


async def search_adzuna(c: httpx.AsyncClient, f: SearchFilters, app_id: str, app_key: str, country: str) -> list[JobListing]:
    params = {"app_id": app_id, "app_key": app_key, "what": f.query, "results_per_page": min(50, f.limit_per_provider), "content-type": "application/json"}
    if f.location:
        params["where"] = f.location
    data = await _get_json(c, f"https://api.adzuna.com/v1/api/jobs/{country}/search/1", params, cache_s=900)
    out = []
    for j in data.get("results", []):
        out.append(JobListing("adzuna", str(j.get("id")), html_to_text(j.get("title", "")), (j.get("company") or {}).get("display_name"),
                              (j.get("location") or {}).get("display_name"), j.get("redirect_url", ""), html_to_text(j.get("description") or ""),
                              j.get("created"), j.get("contract_time"), None, [(j.get("category") or {}).get("label", "")],
                              description_is_partial=True))
    return [x for x in out if _matches(x, f, client_side_query=False)]


async def search_serpapi(c: httpx.AsyncClient, f: SearchFilters, api_key: str) -> list[JobListing]:
    q = f.query + (" internship" if f.level == "internship" and "intern" not in f.query.lower() else "")
    params = {"engine": "google_jobs", "q": q, "api_key": api_key, "hl": "en"}
    if f.location:
        params["location"] = f.location
    if f.remote_only:
        params["ltype"] = "1"
    data = await _get_json(c, "https://serpapi.com/search.json", params, cache_s=1800)
    if data.get("error"):
        raise RuntimeError(str(data["error"])[:200])
    out = []
    for j in data.get("jobs_results", []):
        links = [o.get("link") for o in (j.get("apply_options") or []) if o.get("link")] + [l.get("link") for l in (j.get("related_links") or []) if l.get("link")]
        url = links[0] if links else (j.get("share_link") or "")
        ext = j.get("detected_extensions") or {}
        out.append(JobListing("serpapi", str(j.get("job_id", ""))[:180], j.get("title", ""), j.get("company_name"), j.get("location"), url,
                              j.get("description") or "", ext.get("posted_at"), ext.get("schedule_type"), bool(ext.get("work_from_home")) or None,
                              [], platform=platform_of(url) if url else None,
                              extra={"via": j.get("via"), "source_links": links[:6]}))
    return [x for x in out if _matches(x, f, client_side_query=False)]


async def run_search(f: SearchFilters, providers: list[str], keys: dict[str, Any]) -> tuple[list[JobListing], dict[str, dict[str, Any]]]:
    status: dict[str, dict[str, Any]] = {}
    results: list[JobListing] = []
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=10), headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                                 follow_redirects=True) as c:
        async def one(p: str) -> None:
            t0 = time.monotonic()
            try:
                if p == "remotive":
                    r = await search_remotive(c, f)
                elif p == "arbeitnow":
                    r = await search_arbeitnow(c, f)
                elif p == "remoteok":
                    r = await search_remoteok(c, f)
                elif p == "greenhouse":
                    if not f.greenhouse_boards:
                        status[p] = {"status": "skipped", "reason": "no Greenhouse board tokens supplied"}
                        return
                    r = await search_greenhouse(c, f)
                elif p == "lever":
                    if not f.lever_sites:
                        status[p] = {"status": "skipped", "reason": "no Lever site ids supplied"}
                        return
                    r = await search_lever(c, f)
                elif p == "adzuna":
                    if not (keys.get("adzuna_app_id") and keys.get("adzuna_app_key")):
                        status[p] = {"status": "not_configured", "reason": "missing ADZUNA_APP_ID / ADZUNA_APP_KEY"}
                        return
                    r = await search_adzuna(c, f, keys["adzuna_app_id"], keys["adzuna_app_key"], keys.get("adzuna_country") or "in")
                elif p == "serpapi":
                    if not keys.get("serpapi_api_key"):
                        status[p] = {"status": "not_configured", "reason": "missing SERPAPI_API_KEY"}
                        return
                    r = await search_serpapi(c, f, keys["serpapi_api_key"])
                else:
                    status[p] = {"status": "unknown_provider"}
                    return
                r = r[: f.limit_per_provider]
                results.extend(r)
                status[p] = {"status": "ok", "count": len(r), "ms": int((time.monotonic() - t0) * 1000),
                             "attribution": PROVIDER_INFO.get(p, {}).get("attribution")}
            except Exception as e:  # noqa: BLE001 - provider failures are isolated and reported
                status[p] = {"status": "error", "error": f"{type(e).__name__}: {str(e)[:200]}", "ms": int((time.monotonic() - t0) * 1000)}

        await asyncio.gather(*(one(p) for p in providers))
    return results, status
