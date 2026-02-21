"""
ClawdBot - Strands ReAct Agent
Supports: OpenAI, MiniMax (OpenAI-compatible endpoint), Anthropic
Switch providers via MODEL_PROVIDER env var.
"""

import datetime
import logging
import os
import re
import subprocess
from contextlib import nullcontext
from typing import Any

from datadog_client import DatadogClientError, fetch_recent_agent_spans, search_spans_by_keyword
from memory import MemoryStore, MemoryStoreError
from workspace import (
    append_markdown_memory,
    create_skill as ws_create_skill,
    get_current_workspace_id,
    memory_get as ws_memory_get,
    memory_search as ws_memory_search,
    provision_workspace_json,
    read_skill as ws_read_skill,
    skill_registry_summary,
)
from strands import Agent, tool
from strands.models.openai import OpenAIModel

try:
    from ddtrace.llmobs import LLMObs
except Exception:  # pragma: no cover
    LLMObs = None

logger = logging.getLogger("clawdbot.tools")
VALID_MEMORY_CATEGORIES = {"preference", "fact", "project", "person", "task", "system"}
_MEMORY_STORE: MemoryStore | None = None
_MEMORY_STORE_ERROR: str | None = None


# ---------------------------------------------------------------------------
# Model factory
# ---------------------------------------------------------------------------


def get_model() -> Any:
    provider = os.getenv("MODEL_PROVIDER", "openai").lower()

    if provider == "openai":
        return OpenAIModel(
            client_args={"api_key": os.environ["OPENAI_API_KEY"]},
            model_id=os.getenv("OPENAI_MODEL", "gpt-4o-mini"),
            params={"max_tokens": 4096, "temperature": 0.7},
        )
    elif provider == "minimax":
        return OpenAIModel(
            client_args={
                "api_key": os.environ["MINIMAX_API_KEY"],
                "base_url": os.getenv("MINIMAX_BASE_URL", "https://api.minimax.chat/v1"),
            },
            model_id=os.getenv("MINIMAX_MODEL", "MiniMax-Text-01"),
            params={"max_tokens": 4096, "temperature": 0.7},
        )
    elif provider == "anthropic":
        from strands.models.anthropic import AnthropicModel

        return AnthropicModel(
            client_args={"api_key": os.environ["ANTHROPIC_API_KEY"]},
            model_id=os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022"),
            max_tokens=4096,
        )
    else:
        raise ValueError(f"Unknown MODEL_PROVIDER: {provider!r}. Valid: openai | minimax | anthropic")


# ---------------------------------------------------------------------------
# Datadog helpers
# ---------------------------------------------------------------------------


def _llmobs_tool_span(name: str) -> Any:
    enabled = (
        LLMObs is not None
        and bool(getattr(LLMObs, "enabled", False))
        and os.getenv("DD_LLMOBS_ENABLED", "0") == "1"
        and bool(os.getenv("DD_API_KEY"))
    )
    if not enabled:
        return nullcontext()
    return LLMObs.tool(name=name)


def _llmobs_annotate(span: Any, *, input_data: Any | None = None, output_data: Any | None = None, tags: dict[str, str] | None = None, metadata: dict[str, Any] | None = None) -> None:
    if LLMObs is None or span is None:
        return
    kwargs: dict[str, Any] = {}
    if input_data is not None:
        kwargs["input_data"] = input_data
    if output_data is not None:
        kwargs["output_data"] = output_data
    if tags is not None:
        kwargs["tags"] = tags
    if metadata is not None:
        kwargs["metadata"] = metadata
    try:
        LLMObs.annotate(span=span, **kwargs)
    except Exception:
        logger.debug("llmobs_annotate_failed", exc_info=True)


# ---------------------------------------------------------------------------
# Memory helpers
# ---------------------------------------------------------------------------


def _get_memory_store() -> MemoryStore:
    global _MEMORY_STORE, _MEMORY_STORE_ERROR
    if _MEMORY_STORE is not None:
        return _MEMORY_STORE
    if _MEMORY_STORE_ERROR is not None:
        raise MemoryStoreError(_MEMORY_STORE_ERROR)
    try:
        _MEMORY_STORE = MemoryStore()
        return _MEMORY_STORE
    except Exception as exc:
        _MEMORY_STORE_ERROR = str(exc)
        raise


def build_memory_context(user_message: str, limit: int = 5) -> str:
    """Build a compact memory context block for a user message."""
    try:
        memories = _get_memory_store().search(user_message, limit=limit)
    except Exception as exc:
        logger.warning("memory_recall_failed error=%s", exc)
        return ""

    if not memories:
        return ""

    lines = ["Relevant memories from past sessions:"]
    for memory in memories:
        category = memory.get("category", "unknown")
        content = str(memory.get("content", "")).strip()
        if content:
            lines.append(f"- [{category}] {content}")
    return "\n".join(lines)


def _extract_candidate_memories(text: str) -> list[tuple[str, str]]:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    memories: list[tuple[str, str]] = []
    for line in lines:
        l = line.lower()
        if "prefer" in l:
            memories.append(("preference", line))
        elif "project" in l or "repo" in l:
            memories.append(("project", line))
        elif re.search(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", line):
            memories.append(("fact", line))
        elif "completed" in l or "deployed" in l or "fixed" in l:
            memories.append(("task", line))
    return memories[:15]


def _extract_entities(text: str) -> list[str]:
    entities = set()
    for ip in re.findall(r"\b\d{1,3}(?:\.\d{1,3}){3}\b", text):
        entities.add(ip)
    for path in re.findall(r"(?:[A-Za-z]:\\[^\s,;]+|/[^\s,;]+)", text):
        entities.add(path)
    for word in re.findall(r"\b[A-Z][a-zA-Z0-9_-]{2,}\b", text):
        entities.add(word)
    return list(entities)[:8]


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@tool
def get_current_time() -> str:
    """Get the current date and time."""
    with _llmobs_tool_span("get_current_time") as span:
        logger.info("tool_call get_current_time")
        out = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _llmobs_annotate(span, output_data={"time": out})
        return out


@tool
def calculator(expression: str) -> str:
    """
    Evaluate a mathematical expression.
    Args:
        expression: Math expression e.g. '2 + 2 * 10' or 'sqrt(144)'
    Returns:
        The calculated result as a string.
    """
    import math

    with _llmobs_tool_span("calculator") as span:
        logger.info("tool_call calculator expression=%s", expression[:300])
        _llmobs_annotate(span, input_data={"expression": expression})
        safe_names = {k: v for k, v in math.__dict__.items() if not k.startswith("_")}
        safe_names.update({"abs": abs, "round": round, "int": int, "float": float})
        try:
            result = str(eval(expression, {"__builtins__": {}}, safe_names))  # noqa: S307
            _llmobs_annotate(span, output_data={"result": result})
            return result
        except Exception as e:
            out = f"Error: {e}"
            _llmobs_annotate(span, output_data={"error": str(e)})
            return out


@tool
def run_shell_command(command: str) -> str:
    """
    Run a shell command on the local machine and return its output.
    Args:
        command: Shell command to execute.
    Returns:
        Combined stdout + stderr.
    """
    with _llmobs_tool_span("run_shell_command") as span:
        _llmobs_annotate(span, input_data={"command": command})
        try:
            logger.info("tool_call run_shell_command command=%s", command[:500])
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30  # noqa: S602
            )
            output = (result.stdout + result.stderr).strip() or "(no output)"
            logger.info("tool_result run_shell_command output=%s", output[:800])
            _llmobs_annotate(span, output_data={"output": output}, metadata={"exit_code": result.returncode})
            return output
        except subprocess.TimeoutExpired:
            out = "Error: timed out after 30s"
            _llmobs_annotate(span, output_data={"error": out})
            return out
        except Exception as e:
            out = f"Error: {e}"
            _llmobs_annotate(span, output_data={"error": str(e)})
            return out


@tool
def read_file(path: str) -> str:
    """
    Read contents of a local file.
    Args:
        path: Path to the file.
    Returns:
        File contents as string.
    """
    with _llmobs_tool_span("read_file") as span:
        _llmobs_annotate(span, input_data={"path": path})
        try:
            logger.info("tool_call read_file path=%s", path)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                logger.info("tool_result read_file chars=%s", len(content))
                _llmobs_annotate(span, output_data={"chars": len(content)})
                return content
        except Exception as e:
            out = f"Error: {e}"
            _llmobs_annotate(span, output_data={"error": str(e)})
            return out


@tool
def write_file(path: str, content: str) -> str:
    """
    Write content to a local file, creating parent dirs if needed.
    Args:
        path: File path to write to.
        content: Text content to write.
    Returns:
        Success or error message.
    """
    with _llmobs_tool_span("write_file") as span:
        _llmobs_annotate(span, input_data={"path": path, "chars": len(content)})
        try:
            logger.info("tool_call write_file path=%s chars=%s", path, len(content))
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            out = f"Wrote {len(content)} chars to {path}"
            _llmobs_annotate(span, output_data={"result": out})
            return out
        except Exception as e:
            out = f"Error: {e}"
            _llmobs_annotate(span, output_data={"error": str(e)})
            return out


@tool
def list_directory(path: str = ".") -> str:
    """
    List files and directories at a given path.
    Args:
        path: Directory to list. Defaults to current directory.
    Returns:
        Formatted directory listing.
    """
    with _llmobs_tool_span("list_directory") as span:
        _llmobs_annotate(span, input_data={"path": path})
        try:
            logger.info("tool_call list_directory path=%s", path)
            entries = sorted(os.listdir(path))
            lines = []
            for e in entries:
                kind = "DIR " if os.path.isdir(os.path.join(path, e)) else "FILE"
                lines.append(f"{kind}  {e}")
            out = "\n".join(lines) if lines else "(empty)"
            _llmobs_annotate(span, output_data={"count": len(entries)})
            return out
        except Exception as e:
            out = f"Error: {e}"
            _llmobs_annotate(span, output_data={"error": str(e)})
            return out


@tool
def save_memory(content: str, category: str, entities: list[str] | None = None, topics: list[str] | None = None) -> str:
    """
    Save a fact or preference to long-term memory.
    Args:
        content: Memory text to store.
        category: One of preference | fact | project | person | task | system.
        entities: Optional entity names for graph linking.
        topics: Optional topic tags.
    Returns:
        Success or error message.
    """
    with _llmobs_tool_span("save_memory") as span:
        clean_category = (category or "").strip().lower()
        _llmobs_annotate(span, input_data={"category": clean_category, "content": content})
        if clean_category not in VALID_MEMORY_CATEGORIES:
            allowed = " | ".join(sorted(VALID_MEMORY_CATEGORIES))
            out = f"Error: invalid category '{category}'. Use: {allowed}"
            _llmobs_annotate(span, output_data={"error": out})
            return out
        try:
            memory_id = _get_memory_store().save(
                content=content,
                category=clean_category,
                entities=list(entities or []),
                topics=list(topics or [clean_category]),
                source="agent_tool",
            )
            out = f"Saved memory [{memory_id}] in category '{clean_category}'."
            _llmobs_annotate(span, output_data={"memory_id": memory_id})
            return out
        except Exception as exc:
            logger.warning("save_memory_failed error=%s", exc)
            out = f"Error: could not save memory ({exc})"
            _llmobs_annotate(span, output_data={"error": str(exc)})
            return out


@tool
def search_memory(query: str, limit: int = 5) -> str:
    """
    Search long-term memory for relevant information.
    Args:
        query: Natural language search query.
        limit: Max results to return (1..20).
    Returns:
        Formatted search results.
    """
    with _llmobs_tool_span("search_memory") as span:
        safe_limit = max(1, min(int(limit), 20))
        _llmobs_annotate(span, input_data={"query": query, "limit": safe_limit})
        try:
            memories = _get_memory_store().search(query, limit=safe_limit)
        except Exception as exc:
            logger.warning("search_memory_failed error=%s", exc)
            out = f"Error: could not search memory ({exc})"
            _llmobs_annotate(span, output_data={"error": str(exc)})
            return out
        if not memories:
            return "No relevant memories found."
        lines = [f"Found {len(memories)} memories:"]
        for idx, memory in enumerate(memories, start=1):
            category = memory.get("category", "unknown")
            content = str(memory.get("content", "")).strip()
            entities_found = memory.get("entities") or []
            suffix = f" (entities: {', '.join(map(str, entities_found[:5]))})" if entities_found else ""
            lines.append(f"{idx}. [{category}] {content}{suffix}")
        out = "\n".join(lines)
        _llmobs_annotate(span, output_data={"found": len(memories)})
        return out


@tool
def provision_workspace(workspace_id: str | None = None) -> str:
    """
    Provision the current workspace folder with core markdown files and skills directory.
    Args:
        workspace_id: Optional explicit workspace id. Defaults to current session workspace.
    Returns:
        JSON summary of provisioned paths.
    """
    with _llmobs_tool_span("provision_workspace") as span:
        chosen = (workspace_id or get_current_workspace_id()).strip()
        _llmobs_annotate(span, input_data={"workspace_id": chosen})
        try:
            result = provision_workspace_json(chosen)
            _llmobs_annotate(span, output_data={"result": "ok"})
            return result
        except Exception as exc:
            out = f"Error: could not provision workspace ({exc})"
            _llmobs_annotate(span, output_data={"error": str(exc)})
            return out


@tool
def remember_note(content: str, target: str = "daily") -> str:
    """
    Save durable markdown learning notes in the workspace memory files.
    Args:
        content: Note text to append.
        target: 'daily' for memory/YYYY-MM-DD.md or 'memory' for MEMORY.md.
    Returns:
        Success message with file path.
    """
    with _llmobs_tool_span("remember_note") as span:
        clean_target = (target or "daily").strip().lower()
        if clean_target not in {"daily", "memory"}:
            return "Error: target must be 'daily' or 'memory'."
        _llmobs_annotate(span, input_data={"target": clean_target, "chars": len(content or "")})
        try:
            path = append_markdown_memory(content, target=clean_target, workspace_id=get_current_workspace_id())
            sync_status = ""
            try:
                memory_id = _get_memory_store().save(
                    content=content,
                    category="system",
                    entities=_extract_entities(content or ""),
                    topics=["workspace_markdown", clean_target],
                    source="workspace_note",
                )
                sync_status = f" Synced to Neo4j memory [{memory_id}]."
            except Exception:
                sync_status = " Neo4j sync skipped."
            out = f"Saved note to {path}.{sync_status}"
            _llmobs_annotate(span, output_data={"path": path})
            return out
        except Exception as exc:
            out = f"Error: could not save note ({exc})"
            _llmobs_annotate(span, output_data={"error": str(exc)})
            return out


@tool
def memory_get(limit_chars: int = 4000) -> str:
    """
    Read markdown memory files from the current workspace.
    Args:
        limit_chars: Total max characters to return.
    Returns:
        Combined markdown content from MEMORY.md and recent daily files.
    """
    with _llmobs_tool_span("memory_get") as span:
        safe_limit = max(500, min(int(limit_chars), 20000))
        _llmobs_annotate(span, input_data={"limit_chars": safe_limit})
        try:
            out = ws_memory_get(get_current_workspace_id(), limit_chars=safe_limit)
            _llmobs_annotate(span, output_data={"chars": len(out)})
            return out
        except Exception as exc:
            return f"Error: could not read markdown memory ({exc})"


@tool
def memory_search(query: str, limit: int = 5) -> str:
    """
    Search markdown memory files in the current workspace.
    Args:
        query: Search term.
        limit: Max matching lines to return.
    Returns:
        Matching lines with source file paths.
    """
    with _llmobs_tool_span("memory_search") as span:
        text = (query or "").strip()
        safe_limit = max(1, min(int(limit), 20))
        _llmobs_annotate(span, input_data={"query": text, "limit": safe_limit})
        if not text:
            return "Error: query is required."
        try:
            matches = ws_memory_search(get_current_workspace_id(), text, limit=safe_limit)
        except Exception as exc:
            return f"Error: could not search markdown memory ({exc})"
        if not matches:
            return "No markdown memory matches found."
        lines = [f"Found {len(matches)} markdown match(es):"]
        for idx, item in enumerate(matches, start=1):
            lines.append(f"{idx}. [{item['path']}] {item['line']}")
        out = "\n".join(lines)
        _llmobs_annotate(span, output_data={"found": len(matches)})
        return out


@tool
def list_skills(limit: int = 20) -> str:
    """
    List skills available to this workspace (workspace-local overrides global).
    Args:
        limit: Max skills to include in the summary.
    Returns:
        Skill registry summary.
    """
    with _llmobs_tool_span("list_skills") as span:
        safe_limit = max(1, min(int(limit), 50))
        _llmobs_annotate(span, input_data={"limit": safe_limit})
        try:
            out = skill_registry_summary(get_current_workspace_id(), limit=safe_limit)
            _llmobs_annotate(span, output_data={"summary_chars": len(out)})
            return out
        except Exception as exc:
            return f"Error: could not list skills ({exc})"


@tool
def read_skill(skill_name: str, max_chars: int = 12000) -> str:
    """
    Load full SKILL.md instructions for a specific skill.
    Args:
        skill_name: Skill directory name.
        max_chars: Max chars to return.
    Returns:
        SKILL.md content or not found message.
    """
    with _llmobs_tool_span("read_skill") as span:
        name = (skill_name or "").strip()
        safe_chars = max(1000, min(int(max_chars), 50000))
        if not name:
            return "Error: skill_name is required."
        _llmobs_annotate(span, input_data={"skill_name": name, "max_chars": safe_chars})
        try:
            out = ws_read_skill(name, workspace_id=get_current_workspace_id(), max_chars=safe_chars)
            _llmobs_annotate(span, output_data={"chars": len(out)})
            return out
        except Exception as exc:
            return f"Error: could not read skill ({exc})"


@tool
def create_skill(skill_name: str, content: str, scope: str = "local") -> str:
    """
    Create or update a skill file as SKILL.md.
    Args:
        skill_name: Skill directory name.
        content: Full SKILL.md markdown content.
        scope: local or global (global requires AGENT_GLOBAL_SKILLS_ROOT).
    Returns:
        Created skill path.
    """
    with _llmobs_tool_span("create_skill") as span:
        name = (skill_name or "").strip()
        if not name:
            return "Error: skill_name is required."
        _llmobs_annotate(span, input_data={"skill_name": name, "scope": scope})
        try:
            result = ws_create_skill(name, content, scope=scope, workspace_id=get_current_workspace_id())
        except Exception as exc:
            return f"Error: could not create skill ({exc})"
        return f"Created skill '{result['name']}' in {result['scope']} scope at {result['path']}"


@tool
def reindex_memory_embeddings(force: bool = True) -> str:
    """
    Rebuild embedding chunks for all Neo4j memories.
    Args:
        force: If true, always reindex. If false, only reindex on config change.
    Returns:
        Reindex status summary.
    """
    with _llmobs_tool_span("reindex_memory_embeddings") as span:
        _llmobs_annotate(span, input_data={"force": bool(force)})
        try:
            result = _get_memory_store().reindex_embeddings(force=bool(force))
        except Exception as exc:
            return f"Error: could not reindex memory embeddings ({exc})"
        return (
            f"Embeddings enabled={result.get('embeddings_enabled')}, "
            f"processed={result.get('processed', 0)}, "
            f"chunks={result.get('chunks', 0)}, "
            f"skipped={result.get('skipped', False)}"
        )


@tool
def memory_embedding_status() -> str:
    """
    Inspect embedding config state and whether reindex is needed.
    Returns:
        Embedding state summary.
    """
    with _llmobs_tool_span("memory_embedding_status") as span:
        try:
            result = _get_memory_store().ensure_embedding_index_state(auto_reindex=False)
        except Exception as exc:
            return f"Error: could not read embedding status ({exc})"
        _llmobs_annotate(span, output_data=result)
        return (
            f"Embeddings enabled={result.get('embeddings_enabled')}, "
            f"config_changed={result.get('changed', False)}, "
            f"reindexed={result.get('reindexed', False)}"
        )


@tool
def query_recent_conversations(hours_back: int = 24, limit: int = 20) -> str:
    """Query Datadog for recent ClawdBot conversation turns."""
    with _llmobs_tool_span("query_recent_conversations") as span:
        safe_hours = max(1, min(int(hours_back), 168))
        safe_limit = max(1, min(int(limit), 50))
        _llmobs_annotate(span, input_data={"hours_back": safe_hours, "limit": safe_limit})
        try:
            spans = fetch_recent_agent_spans(hours_back=safe_hours, limit=safe_limit)
        except DatadogClientError as exc:
            return f"Error querying Datadog: {exc}"
        except Exception as exc:
            return f"Error querying Datadog: {exc}"
        if not spans:
            return f"No conversations found in the last {safe_hours} hours."

        lines = [f"Found {len(spans)} conversation(s) from the last {safe_hours}h:\n"]
        for i, s in enumerate(spans, 1):
            user_text = str(s.get("input", ""))
            bot_text = str(s.get("output", ""))
            lines.append(
                f"{i}. [{s.get('start', '')}]\n"
                f"   User: {user_text[:200]}{'...' if len(user_text) > 200 else ''}\n"
                f"   Bot:  {bot_text[:200]}{'...' if len(bot_text) > 200 else ''}\n"
            )
        out = "\n".join(lines)
        _llmobs_annotate(span, output_data={"found": len(spans)})
        return out


@tool
def search_conversation_history(keyword: str, days_back: int = 7) -> str:
    """Search past ClawdBot conversations in Datadog by keyword."""
    with _llmobs_tool_span("search_conversation_history") as span:
        text = (keyword or "").strip()
        if not text:
            return "Error: keyword is required."
        safe_days = max(1, min(int(days_back), 30))
        hours = safe_days * 24
        _llmobs_annotate(span, input_data={"keyword": text, "days_back": safe_days})
        try:
            results = search_spans_by_keyword(text, hours_back=hours, limit=20)
        except DatadogClientError as exc:
            return f"Error searching Datadog: {exc}"
        except Exception as exc:
            return f"Error searching Datadog: {exc}"
        if not results:
            return f"No past conversations mentioning '{text}' found in the last {safe_days} days."

        lines = [f"Found {len(results)} result(s) mentioning '{text}':\n"]
        for r in results:
            inp = str(r.get("input", ""))
            outp = str(r.get("output", ""))
            lines.append(
                f"[{r.get('start', '')}] ({r.get('kind', '')})\n"
                f"  {inp[:150]}{'...' if len(inp) > 150 else ''}\n"
                f"  -> {outp[:150]}{'...' if len(outp) > 150 else ''}\n"
            )
        _llmobs_annotate(span, output_data={"found": len(results)})
        return "\n".join(lines)


@tool
def extract_memories_from_traces(hours_back: int = 48) -> str:
    """Extract and store memory candidates from Datadog traces into Neo4j."""
    with _llmobs_tool_span("extract_memories_from_traces") as span:
        safe_hours = max(1, min(int(hours_back), 168))
        _llmobs_annotate(span, input_data={"hours_back": safe_hours})
        try:
            spans = fetch_recent_agent_spans(hours_back=safe_hours, limit=50)
        except DatadogClientError as exc:
            return f"Error extracting memories: {exc}"
        except Exception as exc:
            return f"Error extracting memories: {exc}"

        if not spans:
            return "No recent conversations to extract memories from."

        saved = 0
        for item in spans:
            combined = f"{item.get('input', '')}\n{item.get('output', '')}".strip()
            for category, candidate in _extract_candidate_memories(combined):
                try:
                    _get_memory_store().save(
                        content=candidate,
                        category=category,
                        entities=_extract_entities(candidate),
                        topics=[category, "datadog_trace"],
                        source="auto_extract",
                    )
                    saved += 1
                except Exception:
                    logger.debug("extract_memories_save_failed", exc_info=True)

        _llmobs_annotate(span, output_data={"spans_scanned": len(spans), "saved": saved})
        return f"Scanned {len(spans)} conversation spans from the last {safe_hours}h. Saved {saved} memories."


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """You are ClawdBot, an execution-focused assistant that can take actions on the user's machine.

Role:
- Solve user requests by taking concrete actions with tools.
- Prefer doing the work over describing hypothetical steps.
- Be explicit about actions performed, files touched, and command outcomes.

Operating Mode:
- Workspace-first: use the session workspace as the default root for files and memory.
- Keep responses concise, actionable, and accurate.
- If a request is ambiguous, make a reasonable assumption and continue unless high risk.

Tool Policy:
- Use shell/file tools when implementation or verification is needed.
- Use calculator/time tools for exact answers when relevant.
- Prefer short, safe commands and avoid unnecessary long-running operations.
- Before risky or destructive actions (deletes, resets, overwrites), ask for confirmation.

Memory Policy:
- Treat markdown memory as human-readable source-of-truth:
  - Use remember_note for durable notes in MEMORY.md or daily memory files.
  - Use memory_search or memory_get to recall workspace memory.
- Treat Neo4j memory as retrieval/index layer:
  - Use save_memory for durable facts/preferences/projects/people/tasks/system state.
  - Use search_memory for semantic + keyword recall.
- When user provides durable information, persist it proactively.
- Before claiming missing context, check memory tools.

Embedding Policy:
- Use memory_embedding_status to inspect embedding config state.
- Use reindex_memory_embeddings after embedding config changes or when retrieval degrades.
- If embedding features are unavailable, continue with keyword/markdown memory paths.

Skill Policy:
- Use list_skills to discover available skills.
- Use read_skill only when full instructions are required for the current task.
- Use create_skill to persist reusable skills in local or global scope.
- Prefer workspace-local skills when conflicts exist.

Observability Policy:
- Use query_recent_conversations/search_conversation_history for Datadog trace recall.
- Use extract_memories_from_traces when useful to backfill memory from conversation history.

Output Policy:
- Report what was done and why.
- Include concrete artifacts: paths, commands, and persisted memory outcomes.
- Use markdown code blocks for code and command output where helpful.
"""


def create_agent() -> Agent:
    return Agent(
        system_prompt=SYSTEM_PROMPT,
        model=get_model(),
        tools=[
            get_current_time,
            calculator,
            run_shell_command,
            read_file,
            write_file,
            list_directory,
            provision_workspace,
            remember_note,
            memory_get,
            memory_search,
            list_skills,
            read_skill,
            create_skill,
            reindex_memory_embeddings,
            memory_embedding_status,
            save_memory,
            search_memory,
            query_recent_conversations,
            search_conversation_history,
            extract_memories_from_traces,
        ],
    )
