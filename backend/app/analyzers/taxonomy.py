"""Versioned deterministic skill taxonomy and synonym map.

Each skill has:
- key / name / category
- patterns: regexes used to find the skill in job descriptions, resumes and LLM terms
  (case-sensitive where a word is ambiguous, e.g. "React", "Go", "C")
- detector: the repository detector family that can produce auditable evidence for
  it, or None when the skill cannot be assessed from code (soft skills, DSA...)
- leetcode_tags: LeetCode tag slugs that count as PROBLEM_SOLVING_SIGNAL (never code proof)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

TAXONOMY_VERSION = "2026.10.1"

CATEGORIES = [
    "language",
    "framework",
    "api_backend",
    "database",
    "testing",
    "devops",
    "architecture",
    "security",
    "cs_fundamentals",
    "domain",
    "collaboration",
    "other",
]


@dataclass(frozen=True)
class Skill:
    key: str
    name: str
    category: str
    patterns: tuple[tuple[str, bool], ...]
    detector: Optional[str] = None
    leetcode_tags: tuple[str, ...] = field(default_factory=tuple)


def _p(*pats: str, cs: bool = False) -> tuple[tuple[str, bool], ...]:
    return tuple((p, cs) for p in pats)


SKILLS: list[Skill] = [
    # --- core W-PS-2 skills -------------------------------------------------
    Skill("api_development", "API development", "api_backend",
          _p(r"\bREST(ful)?\b", r"\bAPIs?\b", r"\bweb services?\b", r"\bendpoints?\b", r"\bHTTP (services|APIs?)\b",
             r"\bbackend services?\b", r"\bmicroservices?\b", r"\bgRPC\b", r"\bserver[- ]side\b"),
          detector="api_backend"),
    Skill("sql", "SQL & relational databases", "database",
          _p(r"\bSQL\b", r"\bPostgre(SQL|s)\b", r"\bMySQL\b", r"\bSQLite\b", r"\bMariaDB\b", r"\bSQL Server\b",
             r"\brelational databases?\b", r"\bRDBMS\b", r"\bdatabase (design|queries|schema)\b", r"\bORMs?\b",
             r"\bSQLAlchemy\b", r"\bPrisma\b", r"\bHibernate\b", r"\bJPA\b", r"\bdatabases?\b"),
          detector="sql_database", leetcode_tags=("database",)),
    Skill("automated_testing", "Automated testing", "testing",
          _p(r"\b(unit|integration|end[- ]to[- ]end|e2e|automated|regression) tests?\b", r"\btesting\b", r"\bTDD\b",
             r"\bpytest\b", r"\bJest\b", r"\bJUnit\b", r"\bMocha\b", r"\bVitest\b", r"\bCypress\b", r"\bPlaywright\b",
             r"\btest coverage\b", r"\bwrit(e|ing) tests\b", r"\btest suites?\b"),
          detector="automated_testing"),
    Skill("ci_cd", "CI/CD pipelines", "devops",
          _p(r"\bCI\s*/\s*CD\b", r"\bcontinuous (integration|delivery|deployment)\b", r"\bGitHub Actions\b",
             r"\bGitLab CI\b", r"\bJenkins\b", r"\bCircleCI\b", r"\b(build|deployment|release) pipelines?\b"),
          detector="ci_cd"),
    Skill("containerization", "Docker & containers", "devops",
          _p(r"\bDocker(file)?\b", r"\bcontaineri[sz](ed|ation)\b", r"\bcontainers?\b", r"\bdocker[- ]compose\b", r"\bPodman\b"),
          detector="containerization"),
    Skill("deployment", "Deployment & hosting", "devops",
          _p(r"\bdeploy(ment|ing|s)?\b", r"\bproduction (systems|environments?)\b", r"\bHeroku\b", r"\bVercel\b",
             r"\bNetlify\b", r"\bFly\.io\b", r"\bhosting\b", r"\bDevOps\b"),
          detector="deployment"),
    Skill("auth_security", "Authentication & security", "security",
          _p(r"\bauthenticat(ion|e)\b", r"\bauthori[sz]ation\b", r"\bOAuth\s*2?(\.0)?\b", r"\bJWTs?\b", r"\bSSO\b",
             r"\bsecurity\b", r"\bsecure coding\b", r"\bOWASP\b", r"\bencryption\b", r"\bRBAC\b"),
          detector="auth_security"),
    # --- languages ---------------------------------------------------------
    Skill("python", "Python", "language", _p(r"\bPython\s*3?\b"), detector="language:python"),
    Skill("javascript", "JavaScript", "language", _p(r"\bJavaScript\b", r"\bES6\+?\b", r"\bECMAScript\b", r"\bJS\b"), detector="language:javascript"),
    Skill("typescript", "TypeScript", "language", _p(r"\bTypeScript\b", r"\bTS\b(?=[,/ ])"), detector="language:typescript"),
    Skill("java", "Java", "language", _p(r"\bJava\b(?!\s*Script)"), detector="language:java"),
    Skill("go", "Go", "language", _p(r"\bGolang\b", r"\bGo\b(?=\s*(,|/|\)|and\b|or\b|programming|language|developer|services|lang))", cs=True), detector="language:go"),
    Skill("csharp", "C#", "language", _p(r"C#", r"\.NET\b", r"\bdotnet\b", r"\bASP\.NET\b"), detector="language:csharp"),
    Skill("cpp", "C++", "language", _p(r"C\+\+", r"\bCPP\b"), detector="language:cpp"),
    Skill("c", "C", "language", _p(r"(?<![\w#+/])C(?=\s*(/|,)\s*C\+\+)", r"\bC programming\b", r"\bANSI C\b", r"\bembedded C\b", cs=True), detector="language:c"),
    Skill("rust", "Rust", "language", _p(r"\bRust\b", cs=True), detector="language:rust"),
    Skill("kotlin", "Kotlin", "language", _p(r"\bKotlin\b"), detector="language:kotlin"),
    Skill("ruby", "Ruby", "language", _p(r"\bRuby\b(?! on Rails)"), detector="language:ruby"),
    Skill("php", "PHP", "language", _p(r"\bPHP\b"), detector="language:php"),
    Skill("swift", "Swift", "language", _p(r"\bSwift(UI)?\b", cs=True), detector="language:swift"),
    Skill("scala", "Scala", "language", _p(r"\bScala\b"), detector="language:scala"),
    Skill("bash", "Shell scripting", "language", _p(r"\bBash\b", r"\bshell scripting\b", r"\bPowerShell\b"), detector="language:shell"),
    Skill("html_css", "HTML & CSS", "language", _p(r"\bHTML5?\b", r"\bCSS3?\b", r"\bSass\b", r"\bSCSS\b"), detector="language:html_css"),
    # --- frameworks / libraries ---------------------------------------------
    Skill("react", "React", "framework", _p(r"\bReact(\.js|JS)?\b(?!ive)", r"\bReact Native\b", cs=True), detector="library:react"),
    Skill("nextjs", "Next.js", "framework", _p(r"\bNext\.?js\b"), detector="library:nextjs"),
    Skill("vue", "Vue.js", "framework", _p(r"\bVue(\.js|JS)?\b"), detector="library:vue"),
    Skill("angular", "Angular", "framework", _p(r"\bAngular(JS)?\b"), detector="library:angular"),
    Skill("nodejs", "Node.js", "framework", _p(r"\bNode(\.js|JS)\b", r"\bNode\b(?=\s*(,|/|and|backend))", cs=False), detector="library:nodejs"),
    Skill("express", "Express", "framework", _p(r"\bExpress(\.js|JS)?\b", cs=True), detector="library:express"),
    Skill("nestjs", "NestJS", "framework", _p(r"\bNest(\.js|JS)\b"), detector="library:nestjs"),
    Skill("django", "Django", "framework", _p(r"\bDjango\b", r"\bDRF\b"), detector="library:django"),
    Skill("flask", "Flask", "framework", _p(r"\bFlask\b"), detector="library:flask"),
    Skill("fastapi", "FastAPI", "framework", _p(r"\bFastAPI\b"), detector="library:fastapi"),
    Skill("spring", "Spring / Spring Boot", "framework", _p(r"\bSpring\s*(Boot|Framework|MVC|Data|Security)\b", r"\bSpring\b(?=\s*(,|/))", cs=True), detector="library:spring"),
    Skill("rails", "Ruby on Rails", "framework", _p(r"\bRails\b", r"\bRuby on Rails\b"), detector="library:rails"),
    Skill("laravel", "Laravel", "framework", _p(r"\bLaravel\b"), detector="library:laravel"),
    Skill("tailwind", "Tailwind CSS", "framework", _p(r"\bTailwind(\s*CSS)?\b"), detector="library:tailwind"),
    Skill("graphql", "GraphQL", "api_backend", _p(r"\bGraphQL\b", r"\bApollo\b"), detector="library:graphql"),
    Skill("pandas", "pandas / data analysis", "domain", _p(r"\bpandas\b", r"\bNumPy\b", r"\bdata analysis\b", r"\bdata wrangling\b"), detector="library:pandas"),
    Skill("machine_learning", "Machine learning", "domain",
          _p(r"\bmachine learning\b", r"\bML\b", r"\bdeep learning\b", r"\bPyTorch\b", r"\bTensorFlow\b", r"\bscikit[- ]learn\b", r"\bKeras\b", r"\bneural networks?\b"),
          detector="library:ml"),
    Skill("llm_apps", "LLM / generative AI apps", "domain",
          _p(r"\bLLMs?\b", r"\bgenerative AI\b", r"\bGenAI\b", r"\bLangChain\b", r"\bRAG\b", r"\bprompt engineering\b", r"\bOpenAI\b", r"\bagentic\b", r"\bAI agents?\b"),
          detector="library:llm"),
    # --- data stores / infra --------------------------------------------------
    Skill("nosql", "NoSQL databases", "database", _p(r"\bNoSQL\b", r"\bMongoDB\b", r"\bDynamoDB\b", r"\bCassandra\b", r"\bFirestore\b", r"\bCouchDB\b"), detector="library:nosql"),
    Skill("caching", "Caching (Redis etc.)", "architecture", _p(r"\bRedis\b", r"\bMemcached\b", r"\bcach(e|ing)\b"), detector="caching"),
    Skill("message_queues", "Message queues & async jobs", "architecture",
          _p(r"\bKafka\b", r"\bRabbitMQ\b", r"\bmessage (queues?|brokers?)\b", r"\bpub/?sub\b", r"\bCelery\b", r"\bSQS\b", r"\bevent[- ]driven\b"),
          detector="library:mq"),
    Skill("concurrency", "Concurrency & async programming", "cs_fundamentals",
          _p(r"\bconcurren(cy|t)\b", r"\bmulti[- ]?threading\b", r"\basynchronous\b", r"\basync\b", r"\bparallel(ism| processing)?\b"),
          detector="concurrency"),
    Skill("kubernetes", "Kubernetes", "devops", _p(r"\bKubernetes\b", r"\bK8s\b", r"\bHelm\b"), detector="kubernetes"),
    Skill("terraform", "Infrastructure as code", "devops", _p(r"\bTerraform\b", r"\binfrastructure as code\b", r"\bIaC\b", r"\bPulumi\b", r"\bCloudFormation\b", r"\bAnsible\b"), detector="iac"),
    Skill("aws", "AWS", "devops", _p(r"\bAWS\b", r"\bAmazon Web Services\b", r"\bEC2\b", r"\bS3\b", r"\bLambda\b"), detector="cloud:aws"),
    Skill("gcp", "Google Cloud", "devops", _p(r"\bGCP\b", r"\bGoogle Cloud\b", r"\bBigQuery\b", r"\bCloud Run\b"), detector="cloud:gcp"),
    Skill("azure", "Azure", "devops", _p(r"\bAzure\b"), detector="cloud:azure"),
    Skill("cloud", "Cloud platforms", "devops", _p(r"\bcloud (platforms?|services|computing|infrastructure)\b", r"\bcloud[- ]native\b"), detector="cloud:any"),
    Skill("linux", "Linux", "devops", _p(r"\bLinux\b", r"\bUnix\b"), detector=None),
    Skill("git", "Git & version control", "collaboration", _p(r"\bGit\b", r"\bversion control\b", r"\bGitHub\b", r"\bGitLab\b", r"\bpull requests?\b"), detector=None),
    Skill("observability", "Logging & monitoring", "devops",
          _p(r"\bobservability\b", r"\bmonitoring\b", r"\blogging\b", r"\bPrometheus\b", r"\bGrafana\b", r"\bOpenTelemetry\b", r"\bSentry\b"),
          detector="observability"),
    # --- fundamentals / design ------------------------------------------------
    Skill("dsa", "Data structures & algorithms", "cs_fundamentals",
          _p(r"\bdata structures?\b", r"\balgorithms?\b", r"\bDSA\b", r"\bproblem[- ]solving\b", r"\bcomplexity analysis\b", r"\bBig[- ]O\b"),
          detector=None,
          leetcode_tags=("array", "string", "hash-table", "dynamic-programming", "tree", "binary-tree", "graph",
                         "depth-first-search", "breadth-first-search", "sorting", "greedy", "binary-search",
                         "two-pointers", "linked-list", "stack", "heap-priority-queue", "backtracking")),
    Skill("system_design", "System design", "architecture",
          _p(r"\bsystem design\b", r"\bdistributed systems?\b", r"\bscalab(le|ility)\b", r"\bsoftware architecture\b", r"\bdesign patterns?\b", r"\bhigh availability\b",
             r"\blarge[- ]scale (systems|services|distributed systems)\b"),
          detector=None),
    Skill("oop", "Object-oriented design", "cs_fundamentals", _p(r"\bobject[- ]oriented\b", r"\bOOP\b", r"\bOOD\b", r"\bSOLID\b"), detector="oop"),
    Skill("networking", "Networking fundamentals", "cs_fundamentals", _p(r"\bnetworking\b", r"\bTCP/IP\b", r"\bHTTP/?\d?\b(?! (services|APIs?))", r"\bDNS\b", r"\bWebSockets?\b"), detector=None),
    Skill("operating_systems", "Operating systems", "cs_fundamentals", _p(r"\boperating systems?\b"), detector=None),
    # --- collaboration / soft skills (never detectable from code) -------------
    Skill("communication", "Communication", "collaboration", _p(r"\bcommunication skills?\b", r"\b(written|verbal) communication\b", r"\bcommunicat(e|ion)\b"), detector=None),
    Skill("teamwork", "Teamwork & collaboration", "collaboration", _p(r"\bteam ?work\b", r"\bcollaborat(e|ion|ive)\b", r"\bcross[- ]functional\b"), detector=None),
    Skill("agile", "Agile practices", "collaboration", _p(r"\bAgile\b", r"\bScrum\b", r"\bKanban\b", r"\bsprints?\b"), detector=None),
    Skill("code_review", "Code review", "collaboration", _p(r"\bcode reviews?\b"), detector=None),
]

SKILL_BY_KEY: dict[str, Skill] = {s.key: s for s in SKILLS}

_COMPILED: list[tuple[Skill, list[tuple[re.Pattern[str], bool]]]] = []

# Very generic skills: when normalising a short term, prefer a more specific skill.
_GENERIC_KEYS = {"api_development", "deployment", "concurrency", "communication", "teamwork", "caching", "sql", "auth_security"}

# Weak patterns only count when no strong match of a different skill sits right next to them
# (e.g. "databases" in "NoSQL databases" must not become an SQL requirement).
WEAK_PATTERNS = {
    r"\bdatabases?\b", r"\bendpoints?\b", r"\bsecurity\b", r"\bcach(e|ing)\b", r"\basync\b", r"\btesting\b",
    r"\bdeploy(ment|ing|s)?\b", r"\bcommunicat(e|ion)\b", r"\bcontainers?\b", r"\bserver[- ]side\b", r"\bhosting\b",
    r"\bmonitoring\b", r"\blogging\b", r"\bsprints?\b", r"\bJS\b", r"\bAPIs?\b", r"\balgorithms?\b",
}


def get_skill(key: str) -> Skill | None:
    return SKILL_BY_KEY.get(key)


def skill_name(key: str) -> str:
    s = SKILL_BY_KEY.get(key)
    if s:
        return s.name
    if key.startswith("custom:"):
        return key[7:].replace("-", " ").title()
    return key


def detector_supported(key: str) -> bool:
    s = SKILL_BY_KEY.get(key)
    return bool(s and s.detector)


def _compiled() -> list[tuple[Skill, list[tuple[re.Pattern[str], bool]]]]:
    if not _COMPILED:
        for sk in SKILLS:
            _COMPILED.append(
                (sk, [(re.compile(p, 0 if cs else re.IGNORECASE), p in WEAK_PATTERNS) for p, cs in sk.patterns])
            )
    return _COMPILED


def find_skill_mentions(text: str) -> list[tuple[Skill, int, int, str]]:
    """Return (skill, start, end, matched_text) for every taxonomy pattern hit.

    Weak (generic) matches adjacent to a strong match of a different skill are dropped.
    """
    raw: list[tuple[Skill, int, int, str, bool]] = []
    for skill, pats in _compiled():
        for pat, weak in pats:
            for m in pat.finditer(text):
                raw.append((skill, m.start(), m.end(), m.group(0), weak))
    strong = [h for h in raw if not h[4]]
    hits: list[tuple[Skill, int, int, str]] = []
    for sk, a, b, txt, weak in raw:
        if weak and any(o[0].key != sk.key and o[1] - 14 <= b and a <= o[2] + 14 for o in strong):
            continue
        hits.append((sk, a, b, txt))
    hits.sort(key=lambda h: (h[1], -(h[2] - h[1])))
    return hits


def normalize_term(term: str) -> tuple[str, str, str]:
    """Map a free-text skill term to (skill_key, display_name, category).

    Prefers the most specific (non-generic) skill whose pattern matches. Unknown
    terms become custom:<slug> and are marked not detector-supported.
    """
    t = term.strip()
    matches = [h for h in find_skill_mentions(t)]
    if matches:
        specific = [h for h in matches if h[0].key not in _GENERIC_KEYS]
        best = (specific or matches)[0][0]
        return best.key, best.name, best.category
    slug = re.sub(r"[^a-z0-9]+", "-", t.lower()).strip("-")[:60] or "unknown"
    return f"custom:{slug}", t[:80], "other"


def taxonomy_document() -> dict:
    return {
        "version": TAXONOMY_VERSION,
        "skills": [
            {
                "key": s.key,
                "name": s.name,
                "category": s.category,
                "detector": s.detector,
                "patterns": [p for p, _ in s.patterns],
                "leetcode_tags": list(s.leetcode_tags),
            }
            for s in SKILLS
        ],
    }
