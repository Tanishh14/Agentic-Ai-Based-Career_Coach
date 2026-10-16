"""Detectors for configuration/infrastructure files and dependency manifests."""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import Any

import yaml

from .findings import Finding, depth_from_size
from .lexer import strip_hash_comments, strip_sql_comments
from .libraries import skills_for_dependency

TRIVIAL_CMD = re.compile(r"^\s*(echo|printf|ls|pwd|true|exit 0|cat|date|whoami|sleep)\b")
TEST_CMD = re.compile(r"\b(pytest|python -m pytest|python -m unittest|tox|nox|npm (run )?test|yarn test|pnpm (run )?test|npx (jest|vitest|playwright|cypress)|jest|vitest|"
                      r"mvn (-\S+ )*(test|verify)|gradle(w)? test|\./gradlew test|go test|cargo test|dotnet test|rspec|bundle exec rspec|phpunit|make test)\b")
BUILD_CMD = re.compile(r"\b(npm (run )?build|yarn build|pnpm build|mvn (package|install)|gradle(w)? build|go build|cargo build|dotnet build|docker build|docker compose build|python -m build|tsc\b)")
DEPLOY_PAT = re.compile(r"(deploy|vercel|netlify|fly(ctl)? deploy|heroku|render|railway|gh-pages|aws |gcloud |az |kubectl (apply|rollout)|helm (upgrade|install)|"
                        r"docker push|terraform apply|serverless deploy|firebase deploy|pages-deploy|azure/webapps-deploy|aws-actions/|google-github-actions/)", re.I)


def _line_of_substr(text: str, needle: str, default: int = 1) -> int:
    idx = text.find(needle)
    return text.count("\n", 0, idx) + 1 if idx >= 0 else default


def detect_dockerfile(path: str, text: str) -> list[Finding]:
    code = strip_hash_comments(text)
    lines = code.split("\n")
    instr = [(i + 1, ln.strip().split()[0].upper()) for i, ln in enumerate(lines) if ln.strip() and ln.strip().split()[0].isalpha()]
    kinds = [k for _, k in instr]
    if "FROM" not in kinds:
        return [Finding("containerization", "dockerfile", 1, max(1, len(lines)), "dockerfile", "config_present", "trivial", "CODE_CONFIG",
                        "Dockerfile without a FROM instruction is not a buildable image definition.", candidate_status="rejected")]
    notes = []
    score = 0
    if any(k in kinds for k in ("CMD", "ENTRYPOINT")):
        score += 1
        notes.append("start command")
    if any(k in ("COPY", "ADD") for k in kinds):
        score += 1
    if "RUN" in kinds:
        score += 1
    if kinds.count("FROM") > 1:
        score += 1
        notes.append("multi-stage build")
    if "HEALTHCHECK" in kinds:
        score += 1
        notes.append("healthcheck")
    if "USER" in kinds:
        score += 1
        notes.append("non-root user")
    depth = "substantial" if score >= 5 else "intermediate" if score >= 3 else "basic" if score >= 1 else "trivial"
    out = [Finding("containerization", "dockerfile", instr[0][0], instr[-1][0], "dockerfile", "meaningful_implementation" if score >= 3 else "config_present",
                   depth, "CODE_CONFIG", f"Dockerfile with {len(instr)} instructions ({', '.join(notes) or 'basic build'}).",
                   candidate_status="accepted" if depth != "trivial" else "rejected")]
    if "HEALTHCHECK" in kinds:
        hl = next(i for i, k in instr if k == "HEALTHCHECK")
        out.append(Finding("deployment", "dockerfile_healthcheck", hl, hl, "healthcheck", "config_present", "basic", "CODE_CONFIG",
                           "Container healthcheck defined (deployment readiness signal)."))
    return out


def _load_yaml(text: str) -> Any:
    try:
        docs = list(yaml.safe_load_all(text))
        return docs
    except yaml.YAMLError:
        return None


def detect_compose(path: str, text: str) -> list[Finding]:
    docs = _load_yaml(text)
    if not docs or not isinstance(docs[0], dict) or not isinstance(docs[0].get("services"), dict):
        return []
    services = docs[0]["services"]
    real = {k: v for k, v in services.items() if isinstance(v, dict) and (v.get("image") or v.get("build"))}
    if not real:
        return [Finding("containerization", "docker_compose", 1, 10, "compose_file", "config_present", "trivial", "CODE_CONFIG",
                        "Compose file without buildable/image services.", candidate_status="rejected")]
    wiring = sum(1 for v in real.values() if any(k in v for k in ("ports", "depends_on", "environment", "volumes", "healthcheck")))
    s = _line_of_substr(text, "services:")
    return [Finding("containerization", "docker_compose", s, min(s + 40, text.count("\n") + 1), "compose_services",
                    "meaningful_implementation" if len(real) >= 2 else "config_present", depth_from_size(len(real) + wiring, 1, 2, 5), "CODE_CONFIG",
                    f"Compose file defines {len(real)} service(s): {', '.join(list(real)[:6])}.")]


def _workflow_runs(doc: dict) -> tuple[list[tuple[str, str]], list[str]]:
    runs: list[tuple[str, str]] = []
    uses: list[str] = []
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    if not isinstance(jobs, dict):
        return runs, uses
    for jname, job in jobs.items():
        if not isinstance(job, dict):
            continue
        for step in job.get("steps") or []:
            if not isinstance(step, dict):
                continue
            if isinstance(step.get("run"), str):
                runs.append((jname, step["run"]))
            if isinstance(step.get("uses"), str):
                uses.append(step["uses"])
    return runs, uses


def detect_ci(path: str, text: str) -> list[Finding]:
    low = path.lower()
    runs: list[tuple[str, str]] = []
    uses: list[str] = []
    if ".github/workflows/" in low:
        docs = _load_yaml(text)
        if not docs or not isinstance(docs[0], dict):
            return [Finding("ci_cd", "github_actions", 1, 5, "workflow", "config_present", "trivial", "CODE_CONFIG", "Workflow file could not be parsed as YAML.",
                            candidate_status="rejected")]
        runs, uses = _workflow_runs(docs[0])
    elif low.endswith(".gitlab-ci.yml") or "circleci/config" in low or low.endswith("azure-pipelines.yml") or low.endswith("bitbucket-pipelines.yml"):
        docs = _load_yaml(text) or [{}]
        def walk(o: Any) -> None:
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ("script", "run", "before_script") and isinstance(v, (str, list)):
                        for c in (v if isinstance(v, list) else [v]):
                            if isinstance(c, str):
                                runs.append(("job", c))
                            elif isinstance(c, dict) and isinstance(c.get("command"), str):
                                runs.append(("job", c["command"]))
                    else:
                        walk(v)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(docs[0])
    elif low.endswith("jenkinsfile"):
        runs = [("stage", m.group(1)) for m in re.finditer(r"\bsh\s+['\"]([^'\"]+)['\"]", text)]
    else:
        return []
    commands = [c for _, r in runs for c in r.split("\n") if c.strip()]
    meaningful = [c for c in commands if not TRIVIAL_CMD.match(c)]
    tests = [c for c in meaningful if TEST_CMD.search(c)]
    builds = [c for c in meaningful if BUILD_CMD.search(c)]
    deploy = [c for c in meaningful if DEPLOY_PAT.search(c)] + [u for u in uses if DEPLOY_PAT.search(u)]
    non_checkout_uses = [u for u in uses if not u.startswith(("actions/checkout", "actions/cache"))]
    out: list[Finding] = []
    first = _line_of_substr(text, "jobs:") if "jobs:" in text else 1
    end = min(text.count("\n") + 1, first + 60)
    if not meaningful and not non_checkout_uses:
        out.append(Finding("ci_cd", "ci_workflow", first, end, "workflow", "config_present", "trivial", "CODE_CONFIG",
                           "CI workflow only runs trivial commands (echo/ls/true) — not meaningful CI.", candidate_status="rejected"))
        return out
    score = (2 if tests else 0) + (1 if builds else 0) + (2 if deploy else 0) + (1 if len(meaningful) >= 3 else 0) + (1 if non_checkout_uses else 0)
    depth = "substantial" if score >= 5 else "intermediate" if score >= 3 else "basic"
    parts = []
    if tests:
        parts.append(f"runs tests (`{tests[0].strip()[:60]}`)")
    if builds:
        parts.append("builds")
    if deploy:
        parts.append("deploys")
    out.append(Finding("ci_cd", "ci_workflow", first, end, "workflow", "meaningful_implementation" if score >= 3 else "config_present", depth, "CODE_CONFIG",
                       f"CI pipeline with {len(meaningful)} meaningful command(s); {', '.join(parts) or 'setup/lint steps only'}.",
                       meta={"tests": bool(tests), "deploy": bool(deploy)}))
    if tests:
        tl = _line_of_substr(text, tests[0].strip()[:40], first)
        out.append(Finding("automated_testing", "ci_runs_tests", tl, tl, "ci_test_step", "config_present", "basic", "CODE_CONFIG",
                           f"CI executes the test suite: `{tests[0].strip()[:80]}` (supports, but does not replace, test-code evidence)."))
    if deploy:
        target = deploy[0].strip()[:80]
        dl = _line_of_substr(text, target[:30], first)
        out.append(Finding("deployment", "ci_deploy_step", dl, dl + 3, "deploy_step", "meaningful_implementation", "intermediate", "CODE_CONFIG",
                           f"Automated deployment step: `{target}`."))
    return out


PLATFORM_FILES = {"procfile": "Heroku/Procfile", "vercel.json": "Vercel", "netlify.toml": "Netlify", "fly.toml": "Fly.io", "render.yaml": "Render",
                  "app.yaml": "Google App Engine", "railway.json": "Railway", "railway.toml": "Railway", "serverless.yml": "Serverless Framework",
                  "serverless.yaml": "Serverless Framework", "firebase.json": "Firebase Hosting", "amplify.yml": "AWS Amplify", "appspec.yml": "AWS CodeDeploy"}


def detect_platform(path: str, text: str) -> list[Finding]:
    name = PurePosixPath(path).name.lower()
    plat = PLATFORM_FILES.get(name)
    if not plat:
        return []
    content = strip_hash_comments(text).strip()
    if len(content) < 8:
        return [Finding("deployment", "platform_config", 1, 1, "platform_config", "config_present", "trivial", "CODE_CONFIG",
                        f"{plat} config file is essentially empty.", candidate_status="rejected")]
    if name == "procfile" and not re.search(r"^\s*web\s*:\s*\S+", content, re.M):
        return [Finding("deployment", "platform_config", 1, 1, "platform_config", "config_present", "trivial", "CODE_CONFIG",
                        "Procfile has no web process.", candidate_status="rejected")]
    out = [Finding("deployment", "platform_config", 1, min(30, text.count("\n") + 1), "platform_config", "config_present", "basic", "CODE_CONFIG",
                   f"{plat} deployment configuration present.")]
    if name.startswith("serverless") and re.search(r"provider:\s*\n\s*name:\s*aws", text):
        out.append(Finding("aws", "serverless_aws", 1, 20, "cloud_config", "config_present", "basic", "CODE_CONFIG", "Serverless Framework targeting AWS."))
    return out


def detect_k8s(path: str, text: str) -> list[Finding]:
    docs = _load_yaml(text)
    if not docs:
        return []
    kinds = [d.get("kind") for d in docs if isinstance(d, dict) and d.get("apiVersion") and d.get("kind")]
    if not kinds:
        return []
    workload = [k for k in kinds if k in ("Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod")]
    probes = "livenessProbe" in text or "readinessProbe" in text
    depth = "substantial" if workload and probes and len(kinds) >= 3 else "intermediate" if workload else "basic"
    out = [Finding("kubernetes", "k8s_manifest", 1, min(60, text.count("\n") + 1), "k8s_manifest", "meaningful_implementation" if workload else "config_present",
                   depth, "CODE_CONFIG", f"Kubernetes manifest(s): {', '.join(kinds[:6])}{' with health probes' if probes else ''}.")]
    if workload:
        out.append(Finding("deployment", "k8s_workload", 1, min(40, text.count("\n") + 1), "k8s_workload", "config_present", "intermediate", "CODE_CONFIG",
                           f"Kubernetes workload definition ({', '.join(workload)})."))
    return out


def detect_terraform(path: str, text: str) -> list[Finding]:
    code = strip_hash_comments(text)
    res = list(re.finditer(r'^\s*resource\s+"([\w-]+)"\s+"([\w-]+)"', code, re.M))
    provs = set(re.findall(r'^\s*provider\s+"([\w-]+)"', code, re.M)) | {r.group(1).split("_")[0] for r in res}
    if not res and not provs:
        return []
    s = code.count("\n", 0, res[0].start()) + 1 if res else 1
    out = [Finding("terraform", "terraform_resources", s, min(s + 40, text.count("\n") + 1), "iac_resources",
                   "meaningful_implementation" if len(res) >= 2 else "config_present", depth_from_size(len(res), 1, 2, 6), "CODE_CONFIG",
                   f"Terraform defines {len(res)} resource(s) ({', '.join(sorted({r.group(1) for r in res})[:5])}).")]
    for prov, skill in (("aws", "aws"), ("google", "gcp"), ("azurerm", "azure")):
        if prov in provs:
            out.append(Finding(skill, "terraform_provider", s, s + 10, "cloud_iac", "config_present", depth_from_size(len(res), 1, 2, 6), "CODE_CONFIG",
                               f"Infrastructure provisioned on {skill.upper()} via Terraform."))
            out.append(Finding("cloud", "terraform_provider", s, s + 10, "cloud_iac", "config_present", depth_from_size(len(res), 1, 2, 6), "CODE_CONFIG",
                               f"Cloud infrastructure ({prov}) defined as code."))
    return out


def detect_sql_file(path: str, text: str) -> list[Finding]:
    code = strip_sql_comments(text)
    stmts = re.findall(r"\b(CREATE\s+(TABLE|INDEX|VIEW|TRIGGER|FUNCTION)|INSERT\s+INTO|SELECT\s+[\s\S]+?\s+FROM|UPDATE\s+\w+\s+SET|ALTER\s+TABLE|DELETE\s+FROM)\b", code, re.I)
    if not stmts:
        return [Finding("sql", "sql_file", 1, min(10, text.count("\n") + 1), "sql_file", "config_present", "trivial", "CODE_CONFIG",
                        "SQL file contains no statements outside comments.", candidate_status="rejected")]
    first = re.search(r"\b(CREATE|INSERT|SELECT|UPDATE|ALTER|DELETE)\b", code, re.I)
    s = code.count("\n", 0, first.start()) + 1 if first else 1
    creates = sum(1 for st in stmts if st[0].upper().startswith("CREATE"))
    constraints = len(re.findall(r"\b(PRIMARY KEY|FOREIGN KEY|REFERENCES|UNIQUE|INDEX)\b", code, re.I))
    depth = "substantial" if creates >= 3 and constraints >= 3 else "intermediate" if creates >= 1 or len(stmts) >= 3 else "basic"
    return [Finding("sql", "sql_file", s, min(s + 40, text.count("\n") + 1), "sql_schema_or_query", "code_usage", depth, "CODE_CONFIG",
                    f"SQL file with {len(stmts)} statement(s) ({creates} DDL, {constraints} key/constraint clauses).")]


def detect_manifest(path: str, text: str) -> list[Finding]:
    name = PurePosixPath(path).name
    deps: list[tuple[str, int]] = []
    lines = text.split("\n")
    if name == "package.json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        for sec in ("dependencies", "devDependencies", "peerDependencies"):
            for dep in (data.get(sec) or {}):
                deps.append((dep, _line_of_substr(text, f'"{dep}"')))
    elif name.startswith("requirements") or name == "Pipfile":
        for i, ln in enumerate(lines, 1):
            m = re.match(r"^\s*([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*([<>=!~].*)?$", ln.split("#")[0])
            if m and not ln.strip().startswith("-"):
                deps.append((m.group(1), i))
    elif name == "pyproject.toml":
        for i, ln in enumerate(lines, 1):
            m = re.match(r'^\s*"([A-Za-z0-9_.\-]+)(\[[^\]]*\])?\s*([<>=!~;].*)?",?\s*$', ln) or re.match(r"^\s*([A-Za-z0-9_.\-]+)\s*=\s*[\"{\[]", ln)
            if m and m.group(1).lower() not in ("name", "version", "description", "python", "readme", "requires-python", "license", "authors"):
                deps.append((m.group(1), i))
    elif name == "go.mod":
        for i, ln in enumerate(lines, 1):
            m = re.match(r"^\s*(require\s+)?([\w.\-/]+\.[\w.\-/]+)\s+v", ln)
            if m:
                deps.append((m.group(2), i))
    elif name in ("pom.xml",):
        for m in re.finditer(r"<artifactId>([^<]+)</artifactId>", text):
            deps.append((m.group(1), text.count("\n", 0, m.start()) + 1))
    elif name.startswith("build.gradle"):
        for m in re.finditer(r"['\"]([\w.\-]+):([\w.\-]+)(:[^'\"]+)?['\"]", text):
            deps.append((m.group(2), text.count("\n", 0, m.start()) + 1))
    elif name == "Gemfile":
        for m in re.finditer(r"^\s*gem\s+['\"]([\w\-]+)['\"]", text, re.M):
            deps.append((m.group(1), text.count("\n", 0, m.start()) + 1))
    elif name == "composer.json":
        try:
            data = json.loads(text)
            for dep in (data.get("require") or {}):
                deps.append((dep.split("/")[-1], _line_of_substr(text, f'"{dep}"')))
        except json.JSONDecodeError:
            return []
    out: list[Finding] = []
    by_skill: dict[str, list[tuple[str, int]]] = {}
    for dep, ln in deps:
        for skill in skills_for_dependency(dep):
            by_skill.setdefault(skill, []).append((dep, ln))
    for skill, items in by_skill.items():
        ln = items[0][1]
        out.append(Finding(skill, "dependency_manifest", ln, ln, "dependency_declaration", "dependency_detected", "trivial", "DEPENDENCY_DECLARATION",
                           f"Declared dependency: {', '.join(d for d, _ in items[:4])} in {name} (declaration only — not proof of use).",
                           meta={"dependencies": [d for d, _ in items]}))
    return out


def detect_tailwind(path: str, text: str) -> list[Finding]:
    name = PurePosixPath(path).name.lower()
    if name.startswith("tailwind.config") and ("content" in text or "theme" in text):
        return [Finding("tailwind", "tailwind_config", 1, min(30, text.count("\n") + 1), "tailwind_config", "config_present", "basic", "CODE_CONFIG",
                        "Tailwind configuration with content/theme settings.")]
    if path.lower().endswith(".css") and re.search(r"@tailwind\s+(base|components|utilities)|@import\s+['\"]tailwindcss['\"]", text):
        ln = _line_of_substr(text, "tailwind")
        return [Finding("tailwind", "tailwind_directives", ln, ln + 3, "tailwind_css", "code_usage", "basic", "CODE_CONFIG", "Tailwind directives imported in CSS.")]
    return []


def detect_html_css(path: str, text: str) -> list[Finding]:
    low = path.lower()
    if low.endswith((".html", ".htm")):
        tags = len(re.findall(r"<(div|section|main|header|nav|form|table|ul|article|footer|button|input)\b", text, re.I))
        if tags < 5:
            return []
        s = _line_of_substr(text.lower(), "<body", 1)
        return [Finding("html_css", "html_structure", s, s + 30, "html_document", "code_usage", depth_from_size(tags, 5, 20, 60), "CODE_IMPLEMENTATION",
                        f"HTML document with {tags} structural elements.")]
    rules = len(re.findall(r"[^{}]+\{[^{}]*:[^{}]*\}", re.sub(r"/\*[\s\S]*?\*/", "", text)))
    if rules < 5:
        return []
    return [Finding("html_css", "css_rules", 1, min(40, text.count("\n") + 1), "stylesheet", "code_usage", depth_from_size(rules, 5, 25, 80), "CODE_IMPLEMENTATION",
                    f"Stylesheet with {rules} rules.")]


def detect_shell(path: str, text: str) -> list[Finding]:
    code = strip_hash_comments(text)
    cmds = [ln for ln in code.split("\n") if ln.strip() and not TRIVIAL_CMD.match(ln)]
    if len(cmds) < 5:
        return []
    return [Finding("bash", "shell_script", 1, min(40, text.count("\n") + 1), "shell_script", "code_usage", depth_from_size(len(cmds), 5, 15, 40),
                    "CODE_IMPLEMENTATION", f"Shell script with {len(cmds)} commands.")]


def detect_graphql_schema(path: str, text: str) -> list[Finding]:
    types = re.findall(r"^\s*(type|input|enum|interface)\s+\w+", text, re.M)
    if len(types) < 2:
        return []
    return [Finding("graphql", "graphql_schema", 1, min(40, text.count("\n") + 1), "graphql_schema", "code_usage", depth_from_size(len(types), 2, 4, 10),
                    "CODE_CONFIG", f"GraphQL schema with {len(types)} type definitions.")]


def scan_config(path: str, text: str, lang: str | None) -> list[Finding]:
    name = PurePosixPath(path).name.lower()
    low = path.lower()
    n = text.count("\n") + 1
    out: list[Finding] = []
    if lang == "dockerfile":
        out += detect_dockerfile(path, text)
    if name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") or re.match(r"docker-compose\.[\w.-]+\.ya?ml$", name):
        out += detect_compose(path, text)
    if ".github/workflows/" in low or name in (".gitlab-ci.yml", "jenkinsfile", "azure-pipelines.yml", "bitbucket-pipelines.yml") or "circleci/config" in low:
        out += detect_ci(path, text)
    out += detect_platform(path, text)
    if lang == "yaml" and "apiVersion" in text and "kind:" in text and ".github/" not in low:
        out += detect_k8s(path, text)
    if name == "chart.yaml" and "apiVersion" in text:
        out.append(Finding("kubernetes", "helm_chart", 1, min(20, n), "helm_chart", "config_present", "basic", "CODE_CONFIG", "Helm chart definition."))
    if lang == "terraform":
        out += detect_terraform(path, text)
    if "AWSTemplateFormatVersion" in text or ("Resources:" in text and "Type: AWS::" in text):
        out.append(Finding("terraform", "cloudformation", 1, min(40, n), "iac_template", "config_present", "intermediate", "CODE_CONFIG", "AWS CloudFormation template."))
        out.append(Finding("aws", "cloudformation", 1, min(40, n), "cloud_iac", "config_present", "intermediate", "CODE_CONFIG", "AWS resources defined in CloudFormation."))
    if lang == "sql":
        out += detect_sql_file(path, text)
    if name in ("package.json", "requirements.txt", "requirements-dev.txt", "pyproject.toml", "pipfile", "go.mod", "pom.xml", "build.gradle",
                "build.gradle.kts", "gemfile", "composer.json") or (name.startswith("requirements") and name.endswith(".txt")):
        out += detect_manifest(path, text if name != "pipfile" else text)
    out += detect_tailwind(path, text)
    if lang == "html_css" and not low.endswith((".scss", ".sass", ".less")) or low.endswith((".scss", ".sass", ".less")):
        out += detect_html_css(path, text)
    if lang == "shell":
        out += detect_shell(path, text)
    if low.endswith((".graphql", ".gql")):
        out += detect_graphql_schema(path, text)
    return [f.clamp(n) for f in out]
