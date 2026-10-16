"""Best-effort public LeetCode data via its public (unofficial, unversioned) GraphQL endpoint.

Small, failure-isolated queries; each section reports ok/unavailable independently.
Never asks for passwords or session cookies. Results are PROBLEM_SOLVING_SIGNAL only.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from ..errors import AppError

ENDPOINT = "https://leetcode.com/graphql"
QUERY_VERSION = "lc-public-2026.10"

QUERIES = {
    "profile": ("query p($username: String!) { matchedUser(username: $username) { username profile { realName ranking } "
                "submitStatsGlobal { acSubmissionNum { difficulty count } } } }"),
    "tags": ("query t($username: String!) { matchedUser(username: $username) { tagProblemCounts { "
             "advanced { tagName tagSlug problemsSolved } intermediate { tagName tagSlug problemsSolved } "
             "fundamental { tagName tagSlug problemsSolved } } } }"),
    "contest": ("query c($username: String!) { userContestRanking(username: $username) { attendedContestsCount rating globalRanking topPercentage } }"),
    "recent": ("query r($username: String!) { recentAcSubmissionList(username: $username, limit: 15) { id title titleSlug timestamp } }"),
    "languages": ("query l($username: String!) { matchedUser(username: $username) { languageProblemCount { languageName problemsSolved } } }"),
}


def parse_username(value: str) -> str:
    v = value.strip()
    m = re.search(r"leetcode\.com/(?:u/)?([A-Za-z0-9_\-]{1,40})/?", v)
    if m:
        return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-]{1,40}", v):
        return v
    raise AppError("invalid_username", "Enter a LeetCode username or a profile URL like https://leetcode.com/u/<name>/", status=422)


async def fetch_public(username: str) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    status: dict[str, str] = {}
    headers = {"Content-Type": "application/json", "Referer": f"https://leetcode.com/u/{username}/",
               "User-Agent": "CareerCoachEvidenceBot/1.0 (public profile statistics)"}
    async with httpx.AsyncClient(timeout=15, headers=headers) as c:
        for name, q in QUERIES.items():
            try:
                r = await c.post(ENDPOINT, json={"query": q, "variables": {"username": username}})
                if r.status_code != 200:
                    status[name] = f"unavailable (HTTP {r.status_code})"
                    continue
                body = r.json()
                if body.get("errors"):
                    status[name] = f"unavailable ({str(body['errors'][0].get('message', 'error'))[:80]})"
                    continue
                data = body.get("data") or {}
                if name == "profile":
                    u = data.get("matchedUser")
                    if not u:
                        raise AppError("leetcode_user_not_found", f"LeetCode user '{username}' was not found (or the profile is private).", status=404)
                    fields["solved"] = {x["difficulty"]: x["count"] for x in (u.get("submitStatsGlobal") or {}).get("acSubmissionNum", [])}
                    fields["ranking"] = (u.get("profile") or {}).get("ranking")
                elif name == "tags":
                    tpc = (data.get("matchedUser") or {}).get("tagProblemCounts") or {}
                    tags: dict[str, int] = {}
                    names: dict[str, str] = {}
                    for level in ("fundamental", "intermediate", "advanced"):
                        for t in tpc.get(level) or []:
                            tags[t["tagSlug"]] = tags.get(t["tagSlug"], 0) + int(t.get("problemsSolved") or 0)
                            names[t["tagSlug"]] = t["tagName"]
                    fields["tag_counts"] = tags
                    fields["tag_names"] = names
                elif name == "contest":
                    fields["contest"] = data.get("userContestRanking")
                elif name == "recent":
                    fields["recent_accepted"] = data.get("recentAcSubmissionList") or []
                elif name == "languages":
                    fields["languages"] = {x["languageName"]: x["problemsSolved"] for x in (data.get("matchedUser") or {}).get("languageProblemCount") or []}
                status[name] = "ok"
            except AppError:
                raise
            except Exception as e:  # noqa: BLE001
                status[name] = f"unavailable ({type(e).__name__})"
    if "solved" not in fields and all(v != "ok" for v in status.values()):
        raise AppError("leetcode_unavailable", "LeetCode's public endpoint did not return data right now.", status=502,
                       action="Retry later, or enter your stats manually (they will be labelled user-supplied).", detail=status)
    return {"fields": fields, "field_status": status, "query_version": QUERY_VERSION}
