"""Job ingestion (URL / paste / upload / provider), requirement persistence and discovery."""
from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import httpx
from sqlalchemy import select

from ..agents.jd_extractor import EXTRACTOR_VERSION, deterministic_extract, extract
from ..agents.orchestrator import AgentRun
from ..config import get_settings
from ..database import session_scope, utcnow
from ..errors import AppError
from ..integrations.job_sources import PROVIDER_INFO, JobListing, SearchFilters, platform_of, run_search
from ..integrations.safe_fetch import USER_AGENT, extract_job_page, html_to_text, normalize_text, safe_get, validate_public_url
from ..models import EvidenceFreshnessEvent, JobPosting, JobSearchRun, MarketObservation, Requirement
from ..security import sha256_hex
from .events import audit, emit
from .integrations_store import provider_config, provider_secret

KEEP_QUERY = {"gh_jid", "jk", "currentjobid", "jobid", "job_id", "id", "lever-source", "jid"}


def canonical_url(url: str | None) -> str | None:
    if not url:
        return None
    p = urlparse(url.strip())
    if not p.scheme or not p.netloc:
        return None
    q = {k: v for k, v in parse_qs(p.query).items() if k.lower() in KEEP_QUERY}
    path = re.sub(r"/+$", "", p.path) or "/"
    return urlunparse((p.scheme.lower(), p.netloc.lower().removeprefix("www."), path, "", urlencode(q, doseq=True), ""))


async def _greenhouse_api(board: str, job_id: str) -> tuple[str, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        r = await c.get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}")
        if r.status_code == 404:
            raise AppError("posting_not_found", "Greenhouse says this posting no longer exists.", status=404)
        r.raise_for_status()
        j = r.json()
    meta = {"title": j.get("title"), "company": j.get("company_name") or board, "location": (j.get("location") or {}).get("name"),
            "posted_at": j.get("first_published") or j.get("updated_at"), "external_id": f"{board}:{job_id}", "provider": "greenhouse",
            "absolute_url": j.get("absolute_url")}
    return html_to_text(html.unescape(j.get("content") or "")), meta


async def _lever_api(site: str, pid: str) -> tuple[str, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        r = await c.get(f"https://api.lever.co/v0/postings/{site}/{pid}")
        if r.status_code == 404:
            raise AppError("posting_not_found", "Lever says this posting no longer exists.", status=404)
        r.raise_for_status()
        j = r.json()
    cats = j.get("categories") or {}
    lists = "\n".join(f"{l.get('text', '')}\n{html_to_text(l.get('content', ''))}" for l in j.get("lists") or [])
    text = normalize_text("\n\n".join(x for x in (j.get("descriptionPlain") or "", lists, j.get("additionalPlain") or "") if x))
    from ..integrations.job_sources import _iso_from_epoch

    meta = {"title": j.get("text"), "company": site, "location": cats.get("location"), "employment_type": cats.get("commitment"),
            "posted_at": _iso_from_epoch(j.get("createdAt"), ms=True), "external_id": f"{site}:{pid}", "provider": "lever"}
    return text, meta


async def _ashby_api(org: str, job_id: str) -> tuple[str, dict[str, Any]]:
    async with httpx.AsyncClient(timeout=20, headers={"User-Agent": USER_AGENT}) as c:
        r = await c.get(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
        r.raise_for_status()
        jobs = r.json().get("jobs", [])
    j = next((x for x in jobs if str(x.get("id")) == job_id), None)
    if not j:
        raise AppError("posting_not_found", "Ashby board has no posting with this id.", status=404)
    meta = {"title": j.get("title"), "company": org, "location": j.get("location"), "employment_type": j.get("employmentType"),
            "posted_at": j.get("publishedAt"), "external_id": f"{org}:{job_id}", "provider": "ashby"}
    return html_to_text(j.get("descriptionHtml") or "") or (j.get("descriptionPlain") or ""), meta


async def fetch_posting(url: str, run: AgentRun | None = None) -> tuple[str, dict[str, Any], list[str], str]:
    """Return (text, metadata, warnings, ingestion_method) for a URL, never bypassing protections."""
    await validate_public_url(url)
    p = urlparse(url)
    host = (p.hostname or "").lower()
    qs = parse_qs(p.query)
    m = re.search(r"/([\w-]+)/jobs/(\d+)", p.path)
    if "greenhouse.io" in host and m:
        text, meta = await _greenhouse_api(m.group(1), m.group(2))
        return text, meta, [], "provider_api"
    if "greenhouse.io" in host and qs.get("for") and qs.get("token"):
        text, meta = await _greenhouse_api(qs["for"][0], qs["token"][0])
        return text, meta, [], "provider_api"
    m = re.search(r"^/([\w-]+)/([0-9a-f-]{36})", p.path)
    if host.endswith("lever.co") and m:
        text, meta = await _lever_api(m.group(1), m.group(2))
        return text, meta, [], "provider_api"
    m = re.search(r"^/([\w.-]+)/([0-9a-f-]{36})", p.path)
    if host == "jobs.ashbyhq.com" and m:
        text, meta = await _ashby_api(m.group(1), m.group(2))
        return text, meta, [], "provider_api"
    res = await safe_get(url)
    if res.content_type == "application/pdf":
        from .documents import extract_text

        doc = extract_text("posting.pdf", "application/pdf", res.body)
        return doc.text, {"provider": platform_of(url)}, [doc.warning] if doc.warning else [], "public_page_fetch"
    text, meta, warnings = extract_job_page(res)
    meta["provider"] = platform_of(res.final_url)
    if res.final_url != url:
        meta["final_url"] = res.final_url
    return text, meta, warnings, "public_page_fetch"


async def ingest(user_id: str, *, method: str, text: str, source_url: str | None, meta: dict[str, Any], warnings: list[str],
                 run: AgentRun, track: bool = True, search_run_id: str | None = None) -> str:
    text = normalize_text(text)
    if len(text) < 40:
        raise AppError("empty_posting", "The job text is empty or too short to analyse.", status=422,
                       action="Paste the full job description or upload it as a file.")
    curl = canonical_url(source_url)
    h = sha256_hex(text)
    platform = platform_of(source_url) if source_url else "user_supplied"
    with session_scope() as db:
        existing = None
        if curl:
            existing = db.execute(select(JobPosting).where(JobPosting.user_id == user_id, JobPosting.canonical_url == curl)).scalar_one_or_none()
        if existing is None and meta.get("external_id"):
            existing = db.execute(select(JobPosting).where(JobPosting.user_id == user_id, JobPosting.provider == meta.get("provider"),
                                                           JobPosting.external_id == meta["external_id"])).scalar_one_or_none()
        if existing is not None:
            changed = existing.content_hash != h
            if changed and existing.status == "analyzed":
                db.add(EvidenceFreshnessEvent(user_id=user_id, kind="job_changed", source_type="job_posting", source_id=existing.id,
                                              reason="The job text changed since it was last analysed; requirements were re-extracted.",
                                              detail={"old_hash": existing.content_hash, "new_hash": h}))
            existing.raw_text = text
            existing.content_hash = h
            existing.retrieved_at = utcnow()
            existing.ingestion_method = method
            existing.extraction_warning = "\n".join(warnings) or None
            existing.meta = {**(existing.meta or {}), "fetch_warnings": warnings}
            for k in ("company", "title", "location", "employment_type", "posted_at"):
                if meta.get(k):
                    setattr(existing, k, str(meta[k])[:300])
            posting_id = existing.id
        else:
            jp = JobPosting(user_id=user_id, search_run_id=search_run_id, source_url=source_url, canonical_url=curl, platform=platform,
                            provider=meta.get("provider") or ("user" if method in ("user_paste", "upload") else platform),
                            ingestion_method=method, external_id=meta.get("external_id"), company=meta.get("company"), title=meta.get("title") or meta.get("page_title"),
                            location=meta.get("location"), employment_type=meta.get("employment_type"), posted_at=meta.get("posted_at"), raw_text=text,
                            content_hash=h, description_is_partial=bool(meta.get("description_is_partial")),
                            extraction_warning="\n".join(warnings) or None, meta={**{k: v for k, v in meta.items() if k not in ("company", "title")}, "fetch_warnings": warnings},
                            status="listing")
            db.add(jp)
            db.flush()
            posting_id = jp.id
    run.record("observe", input_summary=f"{method} {source_url or '(pasted text)'}", result_summary=f"stored {len(text)} chars, sha256 {h[:12]}",
               entity_ids=[posting_id])
    await extract_and_store(user_id, posting_id, run)
    if track:
        set_tracked(user_id, posting_id)
    return posting_id


async def extract_and_store(user_id: str, posting_id: str, run: AgentRun) -> None:
    with session_scope() as db:
        jp = db.get(JobPosting, posting_id)
        text, title, url = jp.raw_text, jp.title, jp.source_url
    res = await run.tool("extract_requirements", f"posting {posting_id[:8]} ({len(text)} chars)", extract, user_id, text, title, run,
                         summarize=lambda r: f"{len(r.requirements)} requirement(s){'; LLM ' + r.llm['model'] if r.llm else '; taxonomy only'}")
    run.record("tool_call", tool="validate_requirement_quotes", input_summary=f"{len(res.requirements) + len(res.rejected)} candidate(s)",
               result_summary=f"{len(res.requirements)} verbatim quotes verified, {len(res.rejected)} rejected")
    with session_scope() as db:
        jp = db.get(JobPosting, posting_id)
        old = db.execute(select(Requirement).where(Requirement.posting_id == posting_id)).scalars().all()
        old_by_key = {r.skill_key: r for r in old}
        keep_ids: set[str] = set()
        for r in res.requirements:
            prev = old_by_key.get(r.skill_key)
            if prev is not None:
                prev.display_name, prev.original_term, prev.category, prev.priority = r.display_name, r.original_term, r.category, r.priority
                prev.quote, prev.char_start, prev.char_end, prev.extraction_method = r.quote, r.char_start, r.char_end, r.method
                prev.extraction_version, prev.confidence, prev.detector_supported, prev.source_url = EXTRACTOR_VERSION, r.confidence, r.detector_supported, url
                keep_ids.add(prev.id)
            else:
                db.add(Requirement(posting_id=posting_id, skill_key=r.skill_key, display_name=r.display_name, original_term=r.original_term,
                                   category=r.category, priority=r.priority, quote=r.quote, char_start=r.char_start, char_end=r.char_end,
                                   source_url=url, extraction_method=r.method, extraction_version=EXTRACTOR_VERSION, confidence=r.confidence,
                                   detector_supported=r.detector_supported))
        for r in old:
            if r.id not in keep_ids:
                db.delete(r)
        jp.status = "analyzed"
        fetch_warn = (jp.meta or {}).get("fetch_warnings") or []
        warn = list(fetch_warn) + res.warnings
        jp.extraction_warning = "\n".join(dict.fromkeys(warn)) or None
        meta = dict(jp.meta or {})
        meta["extraction"] = {"version": EXTRACTOR_VERSION, "llm": res.llm, "rejected": res.rejected[:20], "at": utcnow()}
        jp.meta = meta
        emit("job_analyzed", {"job_posting_id": posting_id, "requirements": len(res.requirements), "warnings": res.warnings}, user_id, db=db)


def set_tracked(user_id: str, posting_id: str) -> None:
    with session_scope() as db:
        for jp in db.execute(select(JobPosting).where(JobPosting.user_id == user_id, JobPosting.is_tracked.is_(True))).scalars():
            jp.is_tracked = False
        jp = db.get(JobPosting, posting_id)
        if jp is None or jp.user_id != user_id:
            raise AppError("not_found", "Job posting not found.", status=404)
        jp.is_tracked = True
        audit("target_job_selected", {"job_posting_id": posting_id}, user_id, db=db)


# ------------------------------------------------------------------ discovery


async def discover(user_id: str, filters: SearchFilters, providers: list[str]) -> dict[str, Any]:
    s = get_settings()
    with session_scope() as db:
        keys = {
            "adzuna_app_id": provider_config(db, user_id, "adzuna").get("app_id") or s.adzuna_app_id,
            "adzuna_app_key": provider_secret(db, user_id, "adzuna", s.adzuna_app_key),
            "adzuna_country": provider_config(db, user_id, "adzuna").get("country") or s.adzuna_country,
            "serpapi_api_key": provider_secret(db, user_id, "serpapi", s.serpapi_api_key),
        }
    listings, status = await run_search(filters, providers, keys)
    seen_urls: set[str] = set()
    seen_fp: set[str] = set()
    unique: list[JobListing] = []
    dups = 0
    for l in listings:
        cu = canonical_url(l.url) or f"{l.provider}:{l.external_id}"
        fp = re.sub(r"\W+", " ", f"{(l.company or '').lower()}|{l.title.lower()}|{(l.location or '').lower()}").strip()
        if cu in seen_urls or fp in seen_fp:
            dups += 1
            continue
        seen_urls.add(cu)
        seen_fp.add(fp)
        unique.append(l)
    with session_scope() as db:
        sr = JobSearchRun(user_id=user_id, filters=filters.__dict__, providers=status, result_count=len(unique), duplicate_count=dups,
                          errors=[{"provider": p, **v} for p, v in status.items() if v.get("status") == "error"])
        db.add(sr)
        db.flush()
        run_id = sr.id
        ids: list[str] = []
        for l in unique:
            cu = canonical_url(l.url)
            existing = None
            if cu:
                existing = db.execute(select(JobPosting).where(JobPosting.user_id == user_id, JobPosting.canonical_url == cu)).scalar_one_or_none()
            if existing is None:
                existing = db.execute(select(JobPosting).where(JobPosting.user_id == user_id, JobPosting.provider == l.provider,
                                                               JobPosting.external_id == l.external_id)).scalar_one_or_none()
            text = normalize_text(l.description)
            if existing is None:
                existing = JobPosting(user_id=user_id, search_run_id=run_id, source_url=l.url, canonical_url=cu, platform=l.platform or platform_of(l.url),
                                      provider=l.provider, ingestion_method="provider_api", external_id=l.external_id, company=l.company,
                                      title=l.title[:300], location=l.location, employment_type=l.employment_type, posted_at=l.posted_at, raw_text=text,
                                      content_hash=sha256_hex(text), description_is_partial=l.description_is_partial, status="listing",
                                      meta={"tags": l.tags[:15], "remote": l.remote, "attribution": PROVIDER_INFO.get(l.provider, {}).get("attribution"),
                                            **l.extra})
                db.add(existing)
                db.flush()
            else:
                existing.search_run_id = run_id
                existing.retrieved_at = utcnow()
            ids.append(existing.id)
            for r in deterministic_extract(text):
                if not db.execute(select(MarketObservation.id).where(MarketObservation.posting_id == existing.id, MarketObservation.skill_key == r.skill_key)).first():
                    db.add(MarketObservation(user_id=user_id, search_run_id=run_id, posting_id=existing.id, skill_key=r.skill_key, priority=r.priority,
                                             provider=l.provider))
    emit("job_search_completed", {"search_run_id": run_id, "results": len(unique), "duplicates": dups}, user_id)
    return {"search_run_id": run_id, "posting_ids": ids, "providers": status, "duplicates": dups}
