"""SSRF-safe HTTP fetching for user-supplied job URLs.

- http/https only; every hop's host is resolved and rejected if private, loopback,
  link-local, multicast, reserved or unspecified (redirects are re-validated).
- robots.txt is honoured; disallowed pages are not fetched (user is asked to paste).
- Response size cap, timeouts, content-type allowlist, HTML sanitised to text.
- No cookies, no login, no stealth headers.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup

from ..errors import AppError

USER_AGENT = "CareerCoachEvidenceBot/1.0 (+job-requirement-analysis; respects robots.txt)"
MAX_BYTES = 3_000_000
MAX_REDIRECTS = 5
ALLOWED_TYPES = ("text/html", "text/plain", "application/json", "application/ld+json", "application/xhtml+xml", "application/pdf")


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    text: str = ""
    structured: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    return not (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or (isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None and not _ip_is_public(str(addr.ipv4_mapped)))
    )


async def validate_public_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise AppError("invalid_url", "Only http(s) URLs can be fetched.", status=422)
    if not parsed.hostname:
        raise AppError("invalid_url", "URL has no host.", status=422)
    if parsed.username or parsed.password:
        raise AppError("invalid_url", "URLs with embedded credentials are not allowed.", status=422)
    host = parsed.hostname
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise AppError("dns_failed", f"Could not resolve host {host}.", status=422)
    ips = {i[4][0] for i in infos}
    if not ips or not all(_ip_is_public(ip) for ip in ips):
        raise AppError("blocked_address", "This URL resolves to a private or reserved network address and will not be fetched.", status=422)
    return url


_robots_cache: dict[str, RobotFileParser | None] = {}


async def robots_allowed(client: httpx.AsyncClient, url: str) -> bool:
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots_cache:
        rp: RobotFileParser | None = RobotFileParser()
        try:
            await validate_public_url(base + "/robots.txt")
            r = await client.get(base + "/robots.txt", follow_redirects=True, timeout=8)
            if r.status_code >= 400:
                rp = None  # no robots file -> allowed
            else:
                rp.parse(r.text.splitlines())  # type: ignore[union-attr]
        except Exception:
            rp = None
        _robots_cache[base] = rp
    rp = _robots_cache[base]
    return True if rp is None else rp.can_fetch(USER_AGENT, url)


async def safe_get(url: str, *, check_robots: bool = True, accept: str = "text/html,application/json;q=0.9,*/*;q=0.5") -> FetchResult:
    await validate_public_url(url)
    headers = {"User-Agent": USER_AGENT, "Accept": accept}
    async with httpx.AsyncClient(headers=headers, timeout=httpx.Timeout(15.0, connect=8.0), follow_redirects=False) as client:
        if check_robots and not await robots_allowed(client, url):
            raise AppError(
                "blocked_by_robots",
                "The site's robots.txt does not permit automated retrieval of this page.",
                status=422,
                action="Paste the job description text or upload it as a PDF/text file. The URL will be kept as provenance.",
            )
        current = url
        for _ in range(MAX_REDIRECTS + 1):
            await validate_public_url(current)
            async with client.stream("GET", current) as resp:
                if resp.status_code in (301, 302, 303, 307, 308):
                    loc = resp.headers.get("location")
                    if not loc:
                        raise AppError("bad_redirect", "Redirect without location.", status=502)
                    current = urljoin(current, loc)
                    continue
                ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
                if resp.status_code in (401, 403):
                    raise AppError("login_required", f"The page returned HTTP {resp.status_code} (login or bot protection).", status=422,
                                   action="Paste the job description or upload it; we never bypass logins or bot protection.")
                if resp.status_code == 404:
                    raise AppError("not_found", "The page was not found (HTTP 404).", status=422)
                if resp.status_code == 429:
                    raise AppError("rate_limited", "The site rate-limited the request (HTTP 429).", status=429, retry_after=int(resp.headers.get("retry-after", "60") or 60))
                if resp.status_code >= 400:
                    raise AppError("fetch_failed", f"The page returned HTTP {resp.status_code}.", status=502)
                if ctype and not ctype.startswith(ALLOWED_TYPES):
                    raise AppError("unsupported_type", f"Unsupported content type {ctype}.", status=422)
                declared = int(resp.headers.get("content-length", "0") or 0)
                if declared > MAX_BYTES:
                    raise AppError("too_large", "The page is larger than the allowed limit.", status=422)
                chunks: list[bytes] = []
                total = 0
                async for chunk in resp.aiter_bytes():
                    total += len(chunk)
                    if total > MAX_BYTES:
                        raise AppError("too_large", "The page is larger than the allowed limit.", status=422)
                    chunks.append(chunk)
                body = b"".join(chunks)
                return FetchResult(url=url, final_url=current, status=resp.status_code, content_type=ctype, body=body)
        raise AppError("too_many_redirects", "Too many redirects.", status=502)


def _jsonld_jobposting(soup: BeautifulSoup) -> dict[str, Any]:
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or "")
        except Exception:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            if isinstance(item, dict) and str(item.get("@type", "")).lower() == "jobposting":
                return item
    return {}


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for t in soup(["script", "style", "noscript", "svg", "iframe", "form", "button", "nav", "footer", "header", "template"]):
        t.decompose()
    for br in soup.find_all(["br"]):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for blk in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "section", "ul", "ol", "tr"]):
        blk.insert_after("\n")
    text = soup.get_text()
    return normalize_text(text)


def normalize_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n")]
    out: list[str] = []
    blank = 0
    for ln in lines:
        if not ln:
            blank += 1
            if blank <= 1 and out:
                out.append("")
            continue
        blank = 0
        out.append(ln)
    return "\n".join(out).strip()


def extract_job_page(result: FetchResult) -> tuple[str, dict[str, Any], list[str]]:
    """Return (text, metadata, warnings) using only values actually present on the page."""
    warnings: list[str] = []
    meta: dict[str, Any] = {}
    if result.content_type in ("application/json", "application/ld+json"):
        return normalize_text(result.body.decode("utf-8", "replace")), meta, warnings
    if result.content_type == "text/plain":
        return normalize_text(result.body.decode("utf-8", "replace")), meta, warnings
    html = result.body.decode("utf-8", "replace")
    soup = BeautifulSoup(html, "lxml")
    jp = _jsonld_jobposting(soup)
    text = ""
    if jp:
        meta["structured_source"] = "schema.org JobPosting (JSON-LD on page)"
        if jp.get("title"):
            meta["title"] = str(jp["title"])[:300]
        org = jp.get("hiringOrganization")
        if isinstance(org, dict) and org.get("name"):
            meta["company"] = str(org["name"])[:300]
        loc = jp.get("jobLocation")
        locs = loc if isinstance(loc, list) else [loc] if loc else []
        parts = []
        for l in locs:
            if isinstance(l, dict):
                addr = l.get("address", {})
                if isinstance(addr, dict):
                    parts.append(", ".join(str(addr[k]) for k in ("addressLocality", "addressRegion", "addressCountry") if addr.get(k) and isinstance(addr.get(k), str)))
        if jp.get("jobLocationType"):
            parts.append(str(jp["jobLocationType"]))
        if any(parts):
            meta["location"] = "; ".join(p for p in parts if p)[:300]
        if jp.get("datePosted"):
            meta["posted_at"] = str(jp["datePosted"])[:60]
        if jp.get("employmentType"):
            et = jp["employmentType"]
            meta["employment_type"] = (", ".join(et) if isinstance(et, list) else str(et))[:100]
        if jp.get("description"):
            text = html_to_text(str(jp["description"]))
    if not text:
        title = soup.find("title")
        if title and title.string and "title" not in meta:
            meta["page_title"] = title.string.strip()[:300]
        text = html_to_text(html)
    if len(text) < 400:
        warnings.append("Very little text was retrieved; the page may require login or render client-side. Paste the description if it looks incomplete.")
    low = text.lower()
    if any(k in low for k in ("sign in to view", "log in to see", "join to apply", "please enable javascript")) and len(text) < 2500:
        warnings.append("The page appears to be behind a login wall or requires JavaScript; retrieved text may be partial.")
    return text, meta, warnings
