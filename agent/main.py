"""
ClawdBot FastAPI Server
Exposes the Strands agent via the AG-UI protocol so CopilotKit can connect.
"""

import json
import logging
import os
import asyncio
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).with_name(".env"))

try:
    from ddtrace.llmobs import LLMObs
except Exception:  # pragma: no cover
    LLMObs = None

if LLMObs is not None and os.getenv("DD_LLMOBS_ENABLED", "1") == "1":
    try:
        LLMObs.enable(
            ml_app=os.getenv("DD_LLMOBS_ML_APP", "clawdbot"),
            api_key=os.getenv("DD_API_KEY"),
            app_key=os.getenv("DD_APP_KEY"),
            site=os.getenv("DD_SITE", "datadoghq.com"),
            agentless_enabled=os.getenv("DD_LLMOBS_AGENTLESS_ENABLED", "1") == "1",
            env=os.getenv("DD_ENV", "development"),
            service=os.getenv("DD_SERVICE", "clawdbot-agent"),
        )
    except Exception:
        pass

import uvicorn
from ag_ui.core import EventType, StateSnapshotEvent, TextMessageContentEvent
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from agent import build_memory_context, create_agent  # noqa: E402
from ag_ui_strands import (  # noqa: E402
    StrandsAgent,
    StrandsAgentConfig,
    ToolBehavior,
    ToolCallContext,
    ToolResultContext,
    add_strands_fastapi_endpoint,
)
from workspace import (  # noqa: E402
    THREAD_ID_CTX,
    build_workspace_context,
    describe_skill_sources,
    provision_workspace,
    skill_registry_summary,
    update_heartbeat,
)

app = FastAPI(title="ClawdBot Agent Server", version="0.1.0")
logger = logging.getLogger("clawdbot")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _now_str() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _truncate(text: str, limit: int = 12000) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n\n... (truncated)"


def _parse_args(args_str: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(args_str or "{}")
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _base_state(state: Dict[str, Any] | None) -> Dict[str, Any]:
    base: Dict[str, Any] = dict(state or {})
    base["canvas_items"] = list(base.get("canvas_items") or [])
    base["thinking_stream"] = str(base.get("thinking_stream") or "")
    return base


class _ThinkTagParser:
    """Incrementally split visible text from <think>...</think> blocks."""

    def __init__(self) -> None:
        self._pending = ""
        self._in_think = False
        self._open_tag = "<think>"
        self._close_tag = "</think>"

    def push(self, chunk: str) -> tuple[str, str]:
        self._pending += chunk
        visible_parts: list[str] = []
        thinking_parts: list[str] = []
        while True:
            if self._in_think:
                end = self._pending.find(self._close_tag)
                if end == -1:
                    keep = max(len(self._close_tag) - 1, 0)
                    if len(self._pending) > keep:
                        thinking_parts.append(self._pending[:-keep])
                        self._pending = self._pending[-keep:]
                    break
                thinking_parts.append(self._pending[:end])
                self._pending = self._pending[end + len(self._close_tag) :]
                self._in_think = False
                continue

            start = self._pending.find(self._open_tag)
            if start == -1:
                keep = max(len(self._open_tag) - 1, 0)
                if len(self._pending) > keep:
                    visible_parts.append(self._pending[:-keep])
                    self._pending = self._pending[-keep:]
                break

            visible_parts.append(self._pending[:start])
            self._pending = self._pending[start + len(self._open_tag) :]
            self._in_think = True

        return "".join(visible_parts), "".join(thinking_parts)

    def flush(self) -> tuple[str, str]:
        if not self._pending:
            return "", ""
        if self._in_think:
            out = self._pending
            self._pending = ""
            return "", out
        out = self._pending
        self._pending = ""
        return out, ""


async def _args_streamer(ctx: ToolCallContext) -> AsyncIterator[str]:
    raw = ctx.args_str or "{}"
    chunk_size = 64
    for i in range(0, len(raw), chunk_size):
        yield raw[i : i + chunk_size]


def _state_from_args(ctx: ToolCallContext) -> Dict[str, Any]:
    logger.info("tool_args tool=%s id=%s args=%s", ctx.tool_name, ctx.tool_use_id, _truncate(ctx.args_str or "{}", 600))
    state = _base_state(getattr(ctx.input_data, "state", None))
    args = _parse_args(ctx.args_str)
    command = str(args.get("command", "")).strip()

    if ctx.tool_name == "run_shell_command":
        state["active_task"] = "Running shell command"
        if command:
            state["last_command"] = command
            state["canvas_items"].append(
                {
                    "type": "output",
                    "title": f"$ {command[:60]}",
                    "content": "(running...)",
                    "language": "bash",
                    "timestamp": _now_str(),
                }
            )
    else:
        state["active_task"] = f"Running {ctx.tool_name}"

    state["canvas_items"] = state["canvas_items"][-20:]
    return state


def _state_from_result(ctx: ToolResultContext) -> Dict[str, Any]:
    logger.info(
        "tool_result tool=%s id=%s result=%s",
        ctx.tool_name,
        ctx.tool_use_id,
        _truncate(str(ctx.result_data or "(no output)"), 600),
    )
    state = _base_state(getattr(ctx.input_data, "state", None))
    args = _parse_args(ctx.args_str)
    result_text = _truncate(str(ctx.result_data or "(no output)"))

    item: Dict[str, Any] | None = None
    if ctx.tool_name == "run_shell_command":
        cmd = str(args.get("command", "")).strip()
        state["last_command"] = cmd
        item = {
            "type": "output",
            "title": f"Command output: {cmd[:60] or 'shell'}",
            "content": result_text,
            "language": "bash",
            "timestamp": _now_str(),
        }
    elif ctx.tool_name == "read_file":
        path = str(args.get("path", "")).strip()
        item = {
            "type": "file",
            "title": f"Read file: {path[:60] or 'file'}",
            "content": result_text,
            "timestamp": _now_str(),
        }
    elif ctx.tool_name == "write_file":
        path = str(args.get("path", "")).strip()
        item = {
            "type": "text",
            "title": f"Write file: {path[:60] or 'file'}",
            "content": result_text,
            "timestamp": _now_str(),
        }
    elif ctx.tool_name in {
        "calculator",
        "list_directory",
        "get_current_time",
        "provision_workspace",
        "remember_note",
        "memory_get",
        "memory_search",
        "list_skills",
        "read_skill",
        "create_skill",
        "reindex_memory_embeddings",
        "memory_embedding_status",
        "save_memory",
        "search_memory",
        "query_recent_conversations",
        "search_conversation_history",
        "extract_memories_from_traces",
    }:
        item = {
            "type": "output",
            "title": ctx.tool_name,
            "content": result_text,
            "timestamp": _now_str(),
        }

    if item:
        state["canvas_items"].append(item)
    state["canvas_items"] = state["canvas_items"][-20:]
    state["active_task"] = None
    return state


def _state_context_builder(input_data: Any, user_message: str) -> str:
    thread_id = str(getattr(input_data, "thread_id", "") or "default")
    provision_workspace(thread_id)
    workspace_context = build_workspace_context(thread_id)
    skill_context = skill_registry_summary(thread_id, limit=20)
    neo4j_context = build_memory_context(user_message, limit=5)

    parts = [
        "Use the following context if relevant to the request.",
        f"Workspace id: {thread_id}",
    ]
    if workspace_context:
        parts.append("Workspace core files:\n" + workspace_context)
    if skill_context:
        parts.append(skill_context)
    if neo4j_context:
        parts.append(neo4j_context)
    parts.append(f"Current user message:\n{user_message}")
    return "\n\n".join(parts)


class TracedStrandsAgent(StrandsAgent):
    """Wrap each AG-UI run in a Datadog LLMObs agent span."""
    _SESSION_LOCKS: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def run(self, input_data: Any) -> AsyncIterator[Any]:
        user_message = ""
        for msg in reversed(getattr(input_data, "messages", []) or []):
            if getattr(msg, "role", "") == "user":
                user_message = str(getattr(msg, "content", "") or "")
                break

        session_id = str(getattr(input_data, "thread_id", "") or "default")
        lock = self._SESSION_LOCKS[session_id]
        provision_workspace(session_id)
        update_heartbeat(session_id, user_message or "(no user message)")
        token = THREAD_ID_CTX.set(session_id)
        output_chunks: list[str] = []
        thinking_chunks: list[str] = []
        parser = _ThinkTagParser()
        try:
            if LLMObs is None or os.getenv("DD_LLMOBS_ENABLED", "1") != "1":
                async with lock:
                    async for event in super().run(input_data):
                        if getattr(event, "type", None) == EventType.TEXT_MESSAGE_CONTENT:
                            raw_delta = str(getattr(event, "delta", "") or "")
                            visible_delta, thinking_delta = parser.push(raw_delta)
                            if thinking_delta:
                                thinking_chunks.append(thinking_delta)
                                state = _base_state(getattr(input_data, "state", None))
                                state["thinking_stream"] = "".join(thinking_chunks)[-12000:]
                                yield StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=state)
                            if visible_delta:
                                output_chunks.append(visible_delta)
                                event.delta = visible_delta
                                yield event
                            continue
                        if getattr(event, "type", None) == EventType.TEXT_MESSAGE_END:
                            tail_visible, tail_thinking = parser.flush()
                            if tail_thinking:
                                thinking_chunks.append(tail_thinking)
                                state = _base_state(getattr(input_data, "state", None))
                                state["thinking_stream"] = "".join(thinking_chunks)[-12000:]
                                yield StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=state)
                            if tail_visible:
                                output_chunks.append(tail_visible)
                                yield TextMessageContentEvent(
                                    type=EventType.TEXT_MESSAGE_CONTENT,
                                    message_id=getattr(event, "message_id", ""),
                                    delta=tail_visible,
                                )
                        yield event
                return

            async with lock:
                with LLMObs.agent(
                    name="clawdbot-react-loop",
                    session_id=session_id,
                    ml_app=os.getenv("DD_LLMOBS_ML_APP", "clawdbot"),
                ) as span:
                    try:
                        LLMObs.annotate(
                            span=span,
                            input_data=[{"role": "user", "content": user_message}],
                            tags={
                                "session_id": session_id,
                                "env": os.getenv("DD_ENV", "development"),
                                "model_provider": os.getenv("MODEL_PROVIDER", "openai"),
                            },
                        )
                    except Exception:
                        pass

                    async for event in super().run(input_data):
                        if getattr(event, "type", None) == EventType.TEXT_MESSAGE_CONTENT:
                            raw_delta = str(getattr(event, "delta", "") or "")
                            visible_delta, thinking_delta = parser.push(raw_delta)
                            if thinking_delta:
                                thinking_chunks.append(thinking_delta)
                                state = _base_state(getattr(input_data, "state", None))
                                state["thinking_stream"] = "".join(thinking_chunks)[-12000:]
                                yield StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=state)
                            if visible_delta:
                                output_chunks.append(visible_delta)
                                event.delta = visible_delta
                                yield event
                            continue
                        if getattr(event, "type", None) == EventType.TEXT_MESSAGE_END:
                            tail_visible, tail_thinking = parser.flush()
                            if tail_thinking:
                                thinking_chunks.append(tail_thinking)
                                state = _base_state(getattr(input_data, "state", None))
                                state["thinking_stream"] = "".join(thinking_chunks)[-12000:]
                                yield StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=state)
                            if tail_visible:
                                output_chunks.append(tail_visible)
                                yield TextMessageContentEvent(
                                    type=EventType.TEXT_MESSAGE_CONTENT,
                                    message_id=getattr(event, "message_id", ""),
                                    delta=tail_visible,
                                )
                        yield event

                    try:
                        LLMObs.annotate(
                            span=span,
                            output_data=[{"role": "assistant", "content": "".join(output_chunks)}],
                        )
                    except Exception:
                        pass
        except Exception:
            async with lock:
                async for event in super().run(input_data):
                    yield event
        finally:
            try:
                clear_state = _base_state(getattr(input_data, "state", None))
                clear_state["thinking_stream"] = ""
                yield StateSnapshotEvent(type=EventType.STATE_SNAPSHOT, snapshot=clear_state)
            except Exception:
                pass
            update_heartbeat(session_id, "".join(output_chunks) or user_message or "(no output)")
            THREAD_ID_CTX.reset(token)


tool_behaviors = {
    "run_shell_command": ToolBehavior(
        args_streamer=_args_streamer,
        state_from_args=_state_from_args,
        state_from_result=_state_from_result,
    ),
    "read_file": ToolBehavior(
        state_from_args=_state_from_args,
        state_from_result=_state_from_result,
    ),
    "write_file": ToolBehavior(
        state_from_args=_state_from_args,
        state_from_result=_state_from_result,
    ),
    "calculator": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "list_directory": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "get_current_time": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "provision_workspace": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "remember_note": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "memory_get": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "memory_search": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "list_skills": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "read_skill": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "create_skill": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "reindex_memory_embeddings": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "memory_embedding_status": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "save_memory": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "search_memory": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "query_recent_conversations": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "search_conversation_history": ToolBehavior(
        state_from_result=_state_from_result,
    ),
    "extract_memories_from_traces": ToolBehavior(
        state_from_result=_state_from_result,
    ),
}

strands_cfg = StrandsAgentConfig(tool_behaviors=tool_behaviors, state_context_builder=_state_context_builder)
strands_agent = TracedStrandsAgent(agent=create_agent(), name="clawdbot", config=strands_cfg)
add_strands_fastapi_endpoint(app, strands_agent, path="/awp")


@app.get("/health")
async def health():
    provider = os.getenv("MODEL_PROVIDER", "openai")
    model = (
        os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if provider == "openai"
        else os.getenv("MINIMAX_MODEL", "MiniMax-Text-01")
        if provider == "minimax"
        else os.getenv("ANTHROPIC_MODEL", "claude-3-5-haiku-20241022")
    )
    return {"status": "ok", "provider": provider, "model": model}


@app.get("/skills")
async def skills(thread_id: str = Query(default="default")):
    provision_workspace(thread_id)
    return describe_skill_sources(thread_id)


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
