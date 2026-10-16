"""GitHub REST API client (official API only).

- Authenticated with a user-supplied token (classic PAT, fine-grained PAT or OAuth token)
  or anonymously for public repositories (60 requests/hour).
- Conditional requests with ETags (304 responses don't consume the rate limit).
- Rate-limit aware: raises ProviderError(retry_after=...) instead of hammering the API.
- Blob contents are content-addressed, so they are cached on disk forever by blob SHA.
"""
from __future__ import annotations

import asyncio
import base64
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from ..config import get_settings
from ..errors import AppError, ProviderError

_etag_cache: "OrderedDict[str, tuple[str, Any]]" = OrderedDict()
_ETAG_MAX = 800
RATE_STATE: dict[str, Any] = {"remaining": None, "limit": None, "reset": None, "updated": None}


class GitHubClient:
    def __init__(self, token: str | None):
        self.token = token
        s = get_settings()
        self.base = s.github_api_url.rstrip("/")
        self.blob_dir = Path(s.blob_cache_dir)
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "career-coach-evidence-agent",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(base_url=self.base, headers=headers, timeout=httpx.Timeout(30, connect=10))
        self._sem = asyncio.Semaphore(8)

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self._client.aclose()

    async def close(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------ core request
    def _record_rate(self, r: httpx.Response) -> None:
        if "x-ratelimit-remaining" in r.headers:
            RATE_STATE.update(
                remaining=int(r.headers.get("x-ratelimit-remaining", 0)),
                limit=int(r.headers.get("x-ratelimit-limit", 0)),
                reset=int(r.headers.get("x-ratelimit-reset", 0)),
                updated=int(time.time()),
                authenticated=bool(self.token),
            )

    async def request(self, method: str, path: str, *, params: dict | None = None, json: Any = None,
                      use_etag: bool = False, accept: str | None = None, allow_404: bool = False) -> tuple[int, Any, httpx.Headers]:
        headers: dict[str, str] = {}
        cache_key = f"{bool(self.token)}:{path}?{sorted((params or {}).items())}"
        if accept:
            headers["Accept"] = accept
        if use_etag and method == "GET" and cache_key in _etag_cache:
            headers["If-None-Match"] = _etag_cache[cache_key][0]
        async with self._sem:
            for attempt in range(3):
                try:
                    r = await self._client.request(method, path, params=params, json=json, headers=headers)
                    break
                except (httpx.ConnectError, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                    if attempt == 2:
                        raise ProviderError("GitHub", f"network error: {type(e).__name__}", transient=True)
                    await asyncio.sleep(1.5 * (attempt + 1))
        self._record_rate(r)
        if r.status_code == 304 and cache_key in _etag_cache:
            _etag_cache.move_to_end(cache_key)
            return 304, _etag_cache[cache_key][1], r.headers
        if r.status_code in (403, 429) and (r.headers.get("x-ratelimit-remaining") == "0" or "rate limit" in r.text.lower()):
            reset = int(r.headers.get("x-ratelimit-reset", time.time() + 60))
            retry = max(5, reset - int(time.time())) if r.headers.get("x-ratelimit-reset") else int(r.headers.get("retry-after", "60"))
            hint = "" if self.token else " Unauthenticated access is limited to 60 requests/hour — connect a GitHub token in Settings."
            raise ProviderError("GitHub", f"API rate limit reached; resets in {retry}s.{hint}", transient=True, retry_after=retry)
        if r.status_code == 401:
            raise AppError("github_unauthorized", "GitHub rejected the token (401). It may be expired or revoked.", status=401,
                           action="Settings → GitHub: paste a new token.")
        if r.status_code == 404:
            if allow_404:
                return 404, None, r.headers
            raise AppError("github_not_found", f"GitHub resource not found or not accessible with the current credentials: {path}", status=404,
                           action="Check the repository name and that the token has access to it (private repos need a token with repo/contents read access).")
        if r.status_code == 403:
            raise AppError("github_forbidden", f"GitHub denied access (403): {r.json().get('message', '') if r.headers.get('content-type','').startswith('application/json') else ''}",
                           status=403, action="Grant the token the required permission for this repository.")
        if r.status_code == 409 and "empty" in r.text.lower():
            raise AppError("github_empty_repo", "The repository is empty (no commits).", status=422)
        if r.status_code == 422:
            raise AppError("github_unprocessable", f"GitHub rejected the request: {r.text[:300]}", status=422)
        if r.status_code >= 500:
            raise ProviderError("GitHub", f"server error {r.status_code}", transient=True)
        data = r.json() if r.content and "json" in r.headers.get("content-type", "") else r.content
        if use_etag and method == "GET" and r.headers.get("etag"):
            _etag_cache[cache_key] = (r.headers["etag"], data)
            _etag_cache.move_to_end(cache_key)
            while len(_etag_cache) > _ETAG_MAX:
                _etag_cache.popitem(last=False)
        return r.status_code, data, r.headers

    async def get(self, path: str, **kw: Any) -> Any:
        return (await self.request("GET", path, **kw))[1]

    # ------------------------------------------------------------ identity / repos
    async def viewer(self) -> dict[str, Any]:
        status, data, headers = await self.request("GET", "/user")
        scopes = [s.strip() for s in headers.get("x-oauth-scopes", "").split(",") if s.strip()]
        return {"login": data["login"], "id": data["id"], "name": data.get("name"), "avatar_url": data.get("avatar_url"),
                "html_url": data.get("html_url"), "scopes": scopes, "token_type": "classic" if headers.get("x-oauth-scopes") is not None else "fine-grained/oauth"}

    async def list_repos(self, max_pages: int = 5) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for page in range(1, max_pages + 1):
            data = await self.get("/user/repos", params={"per_page": 100, "page": page, "sort": "pushed",
                                                         "affiliation": "owner,collaborator,organization_member"}, use_etag=True)
            out.extend(data)
            if len(data) < 100:
                break
        return out

    async def list_public_user_repos(self, username: str) -> list[dict[str, Any]]:
        return await self.get(f"/users/{quote(username)}/repos", params={"per_page": 100, "sort": "pushed", "type": "owner"}, use_etag=True)

    async def get_repo(self, full_name: str) -> dict[str, Any]:
        return await self.get(f"/repos/{full_name}", use_etag=True)

    async def list_branches(self, full_name: str) -> list[dict[str, Any]]:
        return await self.get(f"/repos/{full_name}/branches", params={"per_page": 100}, use_etag=True)

    async def languages(self, full_name: str) -> dict[str, int]:
        return await self.get(f"/repos/{full_name}/languages", use_etag=True)

    async def branch_head(self, full_name: str, branch: str) -> tuple[str, bool]:
        """Return (sha, not_modified). Uses ETag so unchanged polls are free."""
        status, data, _ = await self.request("GET", f"/repos/{full_name}/commits/{quote(branch, safe='')}",
                                             use_etag=True, accept="application/vnd.github.sha")
        sha = data.decode().strip() if isinstance(data, (bytes, bytearray)) else str(data).strip()
        return sha, status == 304

    async def get_commit(self, full_name: str, sha: str) -> dict[str, Any]:
        return await self.get(f"/repos/{full_name}/commits/{sha}", use_etag=True)

    async def get_tree(self, full_name: str, sha: str) -> tuple[list[dict[str, Any]], bool]:
        data = await self.get(f"/repos/{full_name}/git/trees/{sha}", params={"recursive": "1"}, use_etag=True)
        return data.get("tree", []), bool(data.get("truncated"))

    async def compare(self, full_name: str, base: str, head: str) -> dict[str, Any]:
        return await self.get(f"/repos/{full_name}/compare/{base}...{head}", params={"per_page": 100})

    async def get_blob(self, full_name: str, blob_sha: str, path: str | None = None, ref: str | None = None) -> bytes:
        cache = self.blob_dir / blob_sha[:2] / blob_sha
        if cache.exists():
            return cache.read_bytes()
        if not self.token and path and ref:
            # Anonymous access: raw content host avoids the 60 req/h REST quota for public repos.
            async with self._sem:
                r = await self._client.get(f"https://raw.githubusercontent.com/{full_name}/{ref}/{quote(path)}", timeout=30)
            if r.status_code == 404:
                raise AppError("github_not_found", f"File not readable anonymously: {path}", status=404)
            if r.status_code >= 400:
                raise ProviderError("GitHub raw", f"HTTP {r.status_code}", transient=r.status_code >= 500 or r.status_code == 429)
            content = r.content
            import hashlib

            if hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest() != blob_sha:
                raise AppError("blob_mismatch", f"Downloaded content for {path} does not match blob {blob_sha[:8]}.", status=502)
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(content)
            return content
        data = await self.get(f"/repos/{full_name}/git/blobs/{blob_sha}")
        if data.get("encoding") == "base64":
            content = base64.b64decode(data.get("content", ""))
        else:
            content = str(data.get("content", "")).encode()
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_bytes(content)
        return content

    async def file_at_commit(self, full_name: str, path: str, sha: str) -> bytes | None:
        status, data, _ = await self.request("GET", f"/repos/{full_name}/contents/{quote(path)}", params={"ref": sha}, allow_404=True)
        if status == 404 or not isinstance(data, dict):
            return None
        if data.get("sha"):
            cache = self.blob_dir / data["sha"][:2] / data["sha"]
            if cache.exists():
                return cache.read_bytes()
        if data.get("encoding") == "base64":
            return base64.b64decode(data.get("content", ""))
        if data.get("sha"):
            return await self.get_blob(full_name, data["sha"])
        return None

    # ------------------------------------------------------------ writes (always user-confirmed upstream)
    async def create_issue(self, full_name: str, title: str, body: str, labels: list[str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        return (await self.request("POST", f"/repos/{full_name}/issues", json=payload))[1]

    async def comment_issue(self, full_name: str, number: int, body: str) -> dict[str, Any]:
        return (await self.request("POST", f"/repos/{full_name}/issues/{number}/comments", json={"body": body}))[1]

    async def close_issue(self, full_name: str, number: int) -> dict[str, Any]:
        return (await self.request("PATCH", f"/repos/{full_name}/issues/{number}", json={"state": "closed", "state_reason": "completed"}))[1]

    async def get_issue(self, full_name: str, number: int) -> dict[str, Any]:
        return await self.get(f"/repos/{full_name}/issues/{number}")

    async def list_hooks(self, full_name: str) -> list[dict[str, Any]]:
        return await self.get(f"/repos/{full_name}/hooks")

    async def create_hook(self, full_name: str, url: str, secret: str) -> dict[str, Any]:
        payload = {"name": "web", "active": True, "events": ["push"],
                   "config": {"url": url, "content_type": "json", "secret": secret, "insecure_ssl": "0"}}
        return (await self.request("POST", f"/repos/{full_name}/hooks", json=payload))[1]

    async def delete_hook(self, full_name: str, hook_id: str) -> None:
        await self.request("DELETE", f"/repos/{full_name}/hooks/{hook_id}")

    async def download_tarball(self, full_name: str, sha: str) -> bytes:
        r = await self._client.get(f"/repos/{full_name}/tarball/{sha}", follow_redirects=True, timeout=120)
        self._record_rate(r)
        if r.status_code >= 400:
            raise ProviderError("GitHub", f"tarball download failed ({r.status_code})", transient=r.status_code >= 500)
        return r.content


def permalink(full_name: str, sha: str, path: str, start: int, end: int) -> str:
    anchor = f"#L{start}" if start == end else f"#L{start}-L{end}"
    return f"https://github.com/{full_name}/blob/{sha}/{quote(path)}{anchor}"


def commit_url(full_name: str, sha: str) -> str:
    return f"https://github.com/{full_name}/commit/{sha}"


def rate_state() -> dict[str, Any]:
    return dict(RATE_STATE)
