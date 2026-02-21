"""Workspace provisioning and markdown memory helpers."""

from __future__ import annotations

import datetime
import json
import os
import re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

THREAD_ID_CTX: ContextVar[str] = ContextVar("thread_id", default="default")
CORE_FILES = ["AGENTS.md", "SOUL.md", "TOOLS.md", "IDENTITY.md", "USER.md", "HEARTBEAT.md"]
MAX_FILE_CHARS = 2500


def _default_workspace_root() -> Path:
    env_root = os.getenv("AGENT_WORKSPACE_ROOT", "").strip()
    if env_root:
        return Path(env_root).expanduser().resolve()
    return Path(__file__).with_name("workspaces").resolve()


def _sanitize_workspace_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    safe = safe.strip(".-")
    return safe or "default"


def get_current_workspace_id() -> str:
    return _sanitize_workspace_id(THREAD_ID_CTX.get("default"))


def get_workspace_path(workspace_id: str | None = None) -> Path:
    ws_id = _sanitize_workspace_id(workspace_id or get_current_workspace_id())
    return _default_workspace_root() / ws_id


def _workspace_latest_mtime(path: Path) -> float:
    if not path.exists():
        return 0.0
    latest = path.stat().st_mtime
    skills_dir = path / "skills"
    if skills_dir.exists():
        for skill_file in skills_dir.glob("*/SKILL.md"):
            try:
                latest = max(latest, skill_file.stat().st_mtime)
            except Exception:
                continue
    return latest


def resolve_workspace_id(workspace_id: str | None = None) -> str:
    requested = (workspace_id or "").strip()
    if requested and requested.lower() not in {"auto"}:
        return _sanitize_workspace_id(requested)

    forced = os.getenv("AGENT_SKILLS_WORKSPACE_ID", "").strip()
    if forced:
        return _sanitize_workspace_id(forced)

    root = _default_workspace_root()
    if not root.exists():
        return "default"

    candidates = [p for p in root.iterdir() if p.is_dir()]
    if not candidates:
        return "default"
    newest = max(candidates, key=_workspace_latest_mtime)
    return _sanitize_workspace_id(newest.name)


def _default_file_content(filename: str) -> str:
    if filename == "AGENTS.md":
        return "# Agent Workspace\n\nThis file defines local operating instructions for the agent.\n"
    if filename == "SOUL.md":
        return "# Soul\n\nCore behavior, tone, and decision rules for this workspace.\n"
    if filename == "TOOLS.md":
        return "# Tools\n\nDescribe which tools are available and any constraints.\n"
    if filename == "IDENTITY.md":
        return "# Identity\n\nAssistant identity and scope for this workspace.\n"
    if filename == "USER.md":
        return "# User\n\nKnown user preferences and stable context.\n"
    return "# Heartbeat\n\nLast run: never\n"


def _ensure_file(path: Path, content: str) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")


def _default_local_skills() -> dict[str, str]:
    return {
        "windows-powershell-exec": (
            "# windows-powershell-exec\n\n"
            "Purpose: Safely run Windows PowerShell commands for system and project tasks.\n\n"
            "When to use:\n"
            "- User requests shell execution on Windows.\n"
            "- Command requires object parsing with ConvertFrom-Json or Select-Object.\n\n"
            "Steps:\n"
            "1. Prefer `powershell -NoProfile -Command \"...\"`.\n"
            "2. Use native cmdlets (Get-ChildItem, Select-Object, Measure-Object).\n"
            "3. Return command and concise output.\n\n"
            "Examples:\n"
            "- `powershell -NoProfile -Command \"Get-ChildItem -Path . -Recurse -Filter *.py | Measure-Object | Select-Object -ExpandProperty Count\"`\n"
            "- `powershell -NoProfile -Command \"$x=(curl.exe -s 'https://wttr.in/?format=j1' | ConvertFrom-Json); $x.current_condition[0].temp_C\"`\n\n"
            "Constraints:\n"
            "- Avoid Linux-only tools (`jq`, `grep`, `sed`) unless explicitly requested and installed.\n"
            "- Avoid destructive commands without confirmation.\n"
        ),
        "windows-cmd-curl": (
            "# windows-cmd-curl\n\n"
            "Purpose: Fetch live data on Windows using cmd + curl and parse with PowerShell.\n\n"
            "When to use:\n"
            "- User requests web/API data fetch.\n"
            "- Need simple HTTP calls from shell.\n\n"
            "Steps:\n"
            "1. Fetch raw JSON with `cmd /c curl -s <url>` or `curl.exe -s <url>`.\n"
            "2. Parse JSON via PowerShell ConvertFrom-Json when field extraction is needed.\n"
            "3. Surface key facts in concise bullets.\n\n"
            "Examples:\n"
            "- `cmd /c curl -s https://hacker-news.firebaseio.com/v0/topstories.json`\n"
            "- `powershell -NoProfile -Command \"$ids=(curl.exe -s 'https://hacker-news.firebaseio.com/v0/topstories.json' | ConvertFrom-Json); $ids[0]\"`\n\n"
            "Constraints:\n"
            "- Prefer `curl.exe` on Windows to avoid alias ambiguity.\n"
            "- Validate URLs and keep timeouts reasonable.\n"
        ),
    }


def _ensure_default_skills(skills_dir: Path) -> list[str]:
    created: list[str] = []
    for skill_name, content in _default_local_skills().items():
        skill_path = skills_dir / skill_name / "SKILL.md"
        if skill_path.exists():
            continue
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        skill_path.write_text(content, encoding="utf-8")
        created.append(skill_name)
    return created


def provision_workspace(workspace_id: str | None = None) -> dict[str, Any]:
    ws_path = get_workspace_path(workspace_id)
    memory_dir = ws_path / "memory"
    skills_dir = ws_path / "skills"
    ws_path.mkdir(parents=True, exist_ok=True)
    memory_dir.mkdir(parents=True, exist_ok=True)
    skills_dir.mkdir(parents=True, exist_ok=True)

    for core_name in CORE_FILES:
        _ensure_file(ws_path / core_name, _default_file_content(core_name))

    _ensure_file(ws_path / "MEMORY.md", "# Memory\n\nLong-lived notes for this workspace.\n")
    _ensure_file(memory_dir / f"{datetime.date.today().isoformat()}.md", "# Daily Memory\n\n")

    skill_index = skills_dir / "README.md"
    _ensure_file(skill_index, "# Skills\n\nPut skills in subfolders as `skills/<name>/SKILL.md`.\n")
    created_skills = _ensure_default_skills(skills_dir)

    return {
        "workspace_id": ws_path.name,
        "workspace_path": str(ws_path),
        "memory_path": str(ws_path / "MEMORY.md"),
        "daily_memory_path": str(memory_dir / f"{datetime.date.today().isoformat()}.md"),
        "skills_path": str(skills_dir),
        "created_default_skills": created_skills,
    }


def update_heartbeat(workspace_id: str | None, details: str) -> None:
    ws = get_workspace_path(workspace_id)
    hb_path = ws / "HEARTBEAT.md"
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"# Heartbeat\n\nLast run: {now}\n\nLast details:\n{details.strip()[:1200]}\n"
    hb_path.write_text(content, encoding="utf-8")


def build_workspace_context(workspace_id: str | None = None) -> str:
    ws = get_workspace_path(workspace_id)
    if not ws.exists():
        return ""
    blocks: list[str] = []
    for name in CORE_FILES:
        fpath = ws / name
        if not fpath.exists():
            continue
        text = fpath.read_text(encoding="utf-8", errors="ignore").strip()
        if not text:
            continue
        blocks.append(f"[{name}]\n{text[:MAX_FILE_CHARS]}")
    return "\n\n".join(blocks)


def _read_memory_files(workspace_id: str | None = None) -> list[tuple[str, str]]:
    ws = get_workspace_path(workspace_id)
    files: list[tuple[str, str]] = []
    memory_file = ws / "MEMORY.md"
    if memory_file.exists():
        files.append((str(memory_file), memory_file.read_text(encoding="utf-8", errors="ignore")))
    daily_dir = ws / "memory"
    if daily_dir.exists():
        for path in sorted(daily_dir.glob("*.md"), reverse=True):
            files.append((str(path), path.read_text(encoding="utf-8", errors="ignore")))
    return files


def memory_get(workspace_id: str | None = None, limit_chars: int = 4000) -> str:
    safe_limit = max(500, min(int(limit_chars), 20000))
    payload = []
    remaining = safe_limit
    for path, text in _read_memory_files(workspace_id):
        if remaining <= 0:
            break
        chunk = text[:remaining]
        payload.append(f"## {path}\n{chunk}")
        remaining -= len(chunk)
    return "\n\n".join(payload) if payload else "No markdown memories found."


def memory_search(workspace_id: str | None, query: str, limit: int = 5) -> list[dict[str, str]]:
    term = (query or "").strip().lower()
    if not term:
        return []
    safe_limit = max(1, min(int(limit), 20))
    matches: list[dict[str, str]] = []
    for path, text in _read_memory_files(workspace_id):
        for line in text.splitlines():
            if term in line.lower():
                matches.append({"path": path, "line": line.strip()})
                if len(matches) >= safe_limit:
                    return matches
    return matches


def append_markdown_memory(content: str, target: str = "daily", workspace_id: str | None = None) -> str:
    ws = get_workspace_path(workspace_id)
    ws.mkdir(parents=True, exist_ok=True)
    text = (content or "").strip()
    if not text:
        raise ValueError("content is empty")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if target == "memory":
        path = ws / "MEMORY.md"
    else:
        daily_dir = ws / "memory"
        daily_dir.mkdir(parents=True, exist_ok=True)
        path = daily_dir / f"{datetime.date.today().isoformat()}.md"

    with path.open("a", encoding="utf-8") as f:
        f.write(f"\n## {now}\n{text}\n")
    return str(path)


def _load_skill_file(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    first_nonempty = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    return {
        "name": path.parent.name,
        "path": str(path),
        "summary": first_nonempty[:200],
        "preview": text[:4000],
    }


def _get_global_skills_root() -> Path | None:
    env_value = os.getenv("AGENT_GLOBAL_SKILLS_ROOT", "").strip()
    if not env_value:
        return None
    return Path(env_value).expanduser()


def list_local_skills(workspace_id: str | None = None) -> list[dict[str, str]]:
    ws = get_workspace_path(workspace_id)
    local_dir = ws / "skills"
    if not local_dir.exists():
        return []
    results: list[dict[str, str]] = []
    for path in sorted(local_dir.glob("*/SKILL.md")):
        item = _load_skill_file(path)
        item["source"] = "local"
        results.append(item)
    return results


def list_global_skills() -> list[dict[str, str]]:
    global_dir = _get_global_skills_root()
    if not global_dir or not global_dir.exists():
        return []
    results: list[dict[str, str]] = []
    for path in sorted(global_dir.glob("*/SKILL.md")):
        item = _load_skill_file(path)
        item["source"] = "global"
        results.append(item)
    return results


def list_skills(workspace_id: str | None = None) -> list[dict[str, str]]:
    skills_by_name: dict[str, dict[str, str]] = {}
    for item in list_global_skills():
        skills_by_name[item["name"]] = item

    for item in list_local_skills(workspace_id):
        skills_by_name[item["name"]] = item

    return [skills_by_name[name] for name in sorted(skills_by_name)]


def describe_skill_sources(workspace_id: str | None = None) -> dict[str, Any]:
    local_skills = list_local_skills(workspace_id)
    global_skills = list_global_skills()
    effective = list_skills(workspace_id)
    local_names = {item["name"] for item in local_skills}
    effective_enriched: list[dict[str, str]] = []
    for item in effective:
        copy = dict(item)
        copy["source"] = "local" if copy["name"] in local_names else "global"
        effective_enriched.append(copy)
    return {
        "workspace_id": _sanitize_workspace_id(workspace_id or get_current_workspace_id()),
        "local": local_skills,
        "global": global_skills,
        "effective": effective_enriched,
    }


def create_skill(
    skill_name: str,
    content: str,
    scope: str = "local",
    workspace_id: str | None = None,
) -> dict[str, str]:
    name = _sanitize_workspace_id(skill_name)
    text = (content or "").strip()
    if not text:
        raise ValueError("content is empty")

    clean_scope = (scope or "local").strip().lower()
    if clean_scope == "global":
        root = _get_global_skills_root()
        if root is None:
            raise ValueError("AGENT_GLOBAL_SKILLS_ROOT is not configured")
    else:
        ws = get_workspace_path(workspace_id)
        root = ws / "skills"
        root.mkdir(parents=True, exist_ok=True)
        clean_scope = "local"

    skill_path = root / name / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(text, encoding="utf-8")
    return {"name": name, "scope": clean_scope, "path": str(skill_path)}


def read_skill(skill_name: str, workspace_id: str | None = None, max_chars: int = 12000) -> str:
    target = _sanitize_workspace_id(skill_name)
    for skill in list_skills(workspace_id):
        if _sanitize_workspace_id(skill["name"]) == target:
            path = Path(skill["path"])
            text = path.read_text(encoding="utf-8", errors="ignore")
            return text[: max(1000, min(int(max_chars), 50000))]
    return f"Skill not found: {skill_name}"


def skill_registry_summary(workspace_id: str | None = None, limit: int = 15) -> str:
    skills = list_skills(workspace_id)
    if not skills:
        return "No skills available."
    lines = ["Available skills (load full instructions with read_skill):"]
    for skill in skills[: max(1, min(int(limit), 50))]:
        lines.append(f"- {skill['name']}: {skill['summary']}")
    return "\n".join(lines)


def provision_workspace_json(workspace_id: str | None = None) -> str:
    return json.dumps(provision_workspace(workspace_id), indent=2)
