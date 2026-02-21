# ClawdBot × Datadog — Agent Tracing & Memory Mining Guide

> How to instrument the Strands agent with Datadog LLM Observability, trace every
> conversation automatically, and give the agent tools to query its own past traces
> and synthesise them into Neo4j memory.

---

## 1. How Datadog LLM Observability Works (Quick Mental Model)

Every time ClawdBot handles a message, Datadog records a **trace** — a tree of **spans**:

```
Trace (one full conversation turn)
  └─ Agent span  ← the ReAct reasoning loop
       ├─ LLM span    ← the raw model call (input prompt + output + tokens + latency)
       ├─ Tool span   ← e.g. run_shell_command("ls -la")
       ├─ Tool span   ← e.g. calculator("2 ** 32")
       └─ LLM span    ← follow-up model call after tool results
```

Span kinds used in ClawdBot:

| Kind | What it represents |
|---|---|
| `agent` | Root span — the full ReAct loop |
| `llm` | A single call to the model (prompt in, completion out) |
| `tool` | One tool invocation (name, input args, output) |
| `workflow` | Optional grouping for multi-step pipelines |

Each span automatically captures: input/output text, token usage, latency, model name, errors, and any custom metadata you annotate.

---

## 2. Credentials You Need

Go to **[app.datadoghq.com](https://app.datadoghq.com)** → Organisation Settings:

| Key | Where to find it | Used for |
|---|---|---|
| `DD_API_KEY` | Organisation Settings → API Keys | Sending traces (write) |
| `DD_APP_KEY` | Organisation Settings → Application Keys | Querying spans via REST API (read) |
| `DD_SITE` | Shown in your account URL | Routing (e.g. `datadoghq.com`, `us3.datadoghq.com`, `datadoghq.eu`) |

Add to `agent/.env`:

```env
# ─── Datadog ──────────────────────────────────────────────────────────────────
DD_API_KEY=your-api-key-here
DD_APP_KEY=your-app-key-here          # needed for querying spans
DD_SITE=datadoghq.com                 # or us3/us5/eu depending on your account
DD_LLMOBS_ENABLED=1
DD_LLMOBS_ML_APP=clawdbot
DD_LLMOBS_AGENTLESS_ENABLED=1        # set to 1 if NOT running a local DD Agent daemon
DD_ENV=development                    # or production / staging
DD_SERVICE=clawdbot-agent
```

> **Agentless vs Agent mode:** `DD_LLMOBS_AGENTLESS_ENABLED=1` sends traces directly to
> Datadog's API — no local daemon needed. This is the easiest setup for development.
> In production you'd run the Datadog Agent daemon and remove this flag.

---

## 3. Installation

```bash
pip install ddtrace>=3.5.0 datadog-api-client
```

Add to `agent/requirements.txt`:
```
ddtrace>=3.5.0
datadog-api-client>=2.25.0
```

---

## 4. Enabling Tracing in `main.py` (Two Lines)

The cleanest approach is in-code initialisation at the top of `main.py`, before anything else imports:

```python
# agent/main.py  — add at the very top, before other imports

import os
from dotenv import load_dotenv
load_dotenv()

# ── Datadog LLM Observability ─────────────────────────────────────────────────
from ddtrace.llmobs import LLMObs

LLMObs.enable(
    ml_app=os.getenv("DD_LLMOBS_ML_APP", "clawdbot"),
    api_key=os.getenv("DD_API_KEY"),
    site=os.getenv("DD_SITE", "datadoghq.com"),
    agentless_enabled=os.getenv("DD_LLMOBS_AGENTLESS_ENABLED", "1") == "1",
    env=os.getenv("DD_ENV", "development"),
)
# ─────────────────────────────────────────────────────────────────────────────

# ... rest of your imports (FastAPI, agent, etc.)
```

That single `LLMObs.enable()` call activates **automatic instrumentation** for OpenAI, 
Anthropic, and all other supported providers — zero additional code changes needed for
the model calls themselves.

---

## 5. Manual Span Instrumentation for the Strands Agent

Because Strands is not yet a natively supported framework, we wrap the agent turn
manually. This gives us clean `agent` → `llm` + `tool` span hierarchy in Datadog.

### 5a. Wrap the agent turn in `main.py`

```python
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs.utils import Documents

async def run_agent_with_tracing(user_message: str, session_id: str, agent):
    """Run one agent turn, fully traced as an agent span in Datadog."""
    
    with LLMObs.agent(
        name="clawdbot-react-loop",
        session_id=session_id,
        ml_app="clawdbot",
    ) as agent_span:
        # Annotate the root span with the user's message
        LLMObs.annotate(
            span=agent_span,
            input_data=[{"role": "user", "content": user_message}],
            tags={"session_id": session_id, "env": os.getenv("DD_ENV", "dev")},
        )
        
        try:
            result = agent(user_message)
            
            # Annotate with the final output
            LLMObs.annotate(
                span=agent_span,
                output_data=[{"role": "assistant", "content": str(result)}],
            )
            return result
        
        except Exception as e:
            agent_span.set_exc_info(type(e), e, e.__traceback__)
            raise
```

### 5b. Wrap individual tool calls in `agent.py`

For each tool, wrap the body in a tool span so Datadog shows tool name + I/O:

```python
from ddtrace.llmobs import LLMObs

@tool
def run_shell_command(command: str) -> str:
    """Run a shell command on the local machine."""
    with LLMObs.tool(name="run_shell_command") as tool_span:
        LLMObs.annotate(
            span=tool_span,
            input_data={"command": command},
        )
        try:
            result = subprocess.run(
                command, shell=True, capture_output=True, text=True, timeout=30
            )
            output = (result.stdout + result.stderr).strip() or "(no output)"
            LLMObs.annotate(span=tool_span, output_data={"output": output})
            return output
        except Exception as e:
            LLMObs.annotate(span=tool_span, output_data={"error": str(e)})
            return f"Error: {e}"
```

Apply the same `with LLMObs.tool(name="...")` pattern to: `read_file`, `write_file`,
`list_directory`, `calculator`, `save_memory`, `search_memory`.

### 5c. Custom metadata tags (optional but very useful)

Tag any span with structured metadata for filtering in the Datadog UI:

```python
LLMObs.annotate(
    span=span,
    tags={
        "user_id": "local",
        "session_id": session_id,
        "tool_count": str(tool_call_count),
        "model_provider": os.getenv("MODEL_PROVIDER", "openai"),
    },
    metadata={
        "command_executed": command,
        "exit_code": str(result.returncode),
    },
)
```

---

## 6. What Gets Recorded Automatically

Once `LLMObs.enable()` is called, for each OpenAI or Anthropic call Datadog captures:

- **Input messages** — the full prompt array (system + user + prior assistant turns)
- **Output** — the completion text
- **Token usage** — prompt tokens, completion tokens, total
- **Latency** — time-to-first-token and total duration
- **Model name** — e.g. `gpt-4o-mini`, `claude-3-5-haiku-20241022`
- **Errors** — exception type, message, stacktrace
- **Cost estimate** — Datadog calculates this from token counts + known model pricing

All of this is queryable via the Datadog UI and REST API.

---

## 7. Querying Traces — The Two APIs

This is where it gets interesting: the agent can call back into Datadog to read its own
past traces and build memory from them.

There are two ways to query:

### API A — Spans API (APM) — `GET /api/v2/spans/events`
Queries raw APM spans. Works for general span search, returns full span attributes
including input/output text. **Rate limited to 300 requests/hour.**

### API B — Logs API — `GET /api/v2/logs/events`  
LLM Observability also ships trace data as log events, queryable with the Logs API.
Same query syntax, same filters, slightly different response shape.

For memory mining, **the Spans API is the right choice** — it gives you structured
span kind, input/output, metadata, and timing in one response.

---

## 8. Querying Spans from Python

### 8a. Basic setup

```python
import os
import datetime
from datadog_api_client import ApiClient, Configuration
from datadog_api_client.v2.api.spans_api import SpansApi
from datadog_api_client.v2.model.spans_list_request import SpansListRequest
from datadog_api_client.v2.model.spans_list_request_data import SpansListRequestData
from datadog_api_client.v2.model.spans_list_request_filter import SpansListRequestFilter
from datadog_api_client.v2.model.spans_list_request_page import SpansListRequestPage
from datadog_api_client.v2.model.spans_sort import SpansSort

def get_dd_config() -> Configuration:
    config = Configuration()
    config.api_key["apiKeyAuth"]         = os.environ["DD_API_KEY"]
    config.api_key["appKeyAuth"]         = os.environ["DD_APP_KEY"]
    config.server_variables["site"]      = os.getenv("DD_SITE", "datadoghq.com")
    return config
```

### 8b. Fetch recent conversation turns (agent spans)

```python
def fetch_recent_agent_spans(hours_back: int = 24, limit: int = 50) -> list[dict]:
    """
    Fetch the most recent ClawdBot agent-level spans from Datadog.
    Each span = one full conversation turn with user input + agent output.
    """
    config = get_dd_config()
    now = datetime.datetime.utcnow()
    from_time = now - datetime.timedelta(hours=hours_back)

    body = SpansListRequest(
        data=SpansListRequestData(
            filter=SpansListRequestFilter(
                # LLM Observability span kind filter
                query='@span.kind:agent service:clawdbot-agent',
                from_=from_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                to=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
            sort=SpansSort.TIMESTAMP_DESCENDING,
            page=SpansListRequestPage(limit=limit),
        )
    )

    with ApiClient(config) as api_client:
        api = SpansApi(api_client)
        response = api.list_spans(body=body)

    spans = []
    for item in response.data or []:
        attrs = item.attributes
        spans.append({
            "span_id":    item.id,
            "trace_id":   attrs.get("trace_id"),
            "start":      attrs.get("start_timestamp"),
            "end":        attrs.get("end_timestamp"),
            "duration_ms": attrs.get("duration", 0) / 1_000_000,  # ns → ms
            "input":      attrs.get("meta", {}).get("input", {}).get("value", ""),
            "output":     attrs.get("meta", {}).get("output", {}).get("value", ""),
            "session_id": attrs.get("tags", {}).get("session_id", ""),
            "error":      attrs.get("error", {}).get("message", ""),
        })

    return spans
```

### 8c. Fetch tool calls from a specific session

```python
def fetch_tool_spans(session_id: str, hours_back: int = 48) -> list[dict]:
    """
    Fetch all tool-call spans for a given session.
    Useful for understanding what commands or files the agent touched.
    """
    config = get_dd_config()
    now = datetime.datetime.utcnow()
    from_time = now - datetime.timedelta(hours=hours_back)

    query = f'@span.kind:tool service:clawdbot-agent @meta.tags.session_id:{session_id}'

    body = SpansListRequest(
        data=SpansListRequestData(
            filter=SpansListRequestFilter(
                query=query,
                from_=from_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                to=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
            sort=SpansSort.TIMESTAMP_DESCENDING,
            page=SpansListRequestPage(limit=100),
        )
    )

    with ApiClient(config) as api_client:
        response = SpansApi(api_client).list_spans(body=body)

    return [
        {
            "tool_name":  item.attributes.get("resource_name", ""),
            "input":      item.attributes.get("meta", {}).get("input", {}).get("value", ""),
            "output":     item.attributes.get("meta", {}).get("output", {}).get("value", ""),
            "start":      item.attributes.get("start_timestamp"),
            "error":      item.attributes.get("error", {}).get("message"),
        }
        for item in (response.data or [])
    ]
```

### 8d. Search spans by content keyword

```python
def search_spans_by_keyword(keyword: str, hours_back: int = 168) -> list[dict]:
    """
    Full-text search across all span input/output for a keyword.
    168 hours = 7 days. Good for "what did we discuss about X last week?"
    """
    config = get_dd_config()
    now = datetime.datetime.utcnow()
    from_time = now - datetime.timedelta(hours=hours_back)

    # Datadog span search supports wildcard and phrase queries
    query = f'service:clawdbot-agent "{keyword}"'

    body = SpansListRequest(
        data=SpansListRequestData(
            filter=SpansListRequestFilter(
                query=query,
                from_=from_time.strftime("%Y-%m-%dT%H:%M:%SZ"),
                to=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            ),
            sort=SpansSort.TIMESTAMP_DESCENDING,
            page=SpansListRequestPage(limit=20),
        )
    )

    with ApiClient(config) as api_client:
        response = SpansApi(api_client).list_spans(body=body)

    return [
        {
            "kind":   item.attributes.get("type", ""),
            "input":  item.attributes.get("meta", {}).get("input", {}).get("value", ""),
            "output": item.attributes.get("meta", {}).get("output", {}).get("value", ""),
            "start":  item.attributes.get("start_timestamp"),
        }
        for item in (response.data or [])
    ]
```

---

## 9. Agent Tools for Datadog

These become Strands `@tool` functions the agent can call itself during a conversation
to look up its own history and create memory from it.

### `query_recent_conversations`

```python
@tool
def query_recent_conversations(hours_back: int = 24, limit: int = 20) -> str:
    """
    Query Datadog for recent ClawdBot conversation turns.
    Returns a summary of what was discussed and what tools were used.
    Use this to recall past interactions or find context for the current request.

    Args:
        hours_back: How far back to search (default 24h, max 168h / 7 days)
        limit:      Max number of conversations to return (default 20)

    Returns:
        Formatted summary of recent conversations.
    """
    with LLMObs.tool(name="query_recent_conversations") as span:
        try:
            spans = fetch_recent_agent_spans(hours_back=min(hours_back, 168), limit=limit)
            if not spans:
                return f"No conversations found in the last {hours_back} hours."

            lines = [f"Found {len(spans)} conversation(s) from the last {hours_back}h:\n"]
            for i, s in enumerate(spans, 1):
                lines.append(
                    f"{i}. [{s['start']}]\n"
                    f"   User: {s['input'][:200]}{'...' if len(s['input']) > 200 else ''}\n"
                    f"   Bot:  {s['output'][:200]}{'...' if len(s['output']) > 200 else ''}\n"
                )

            result = "\n".join(lines)
            LLMObs.annotate(span=span, input_data={"hours_back": hours_back},
                            output_data={"found": len(spans)})
            return result

        except Exception as e:
            return f"Error querying Datadog: {e}"
```

### `search_conversation_history`

```python
@tool
def search_conversation_history(keyword: str, days_back: int = 7) -> str:
    """
    Search past ClawdBot conversations in Datadog for a specific keyword or topic.
    Use this to find what was previously discussed or done related to a subject.

    Args:
        keyword:   The word or phrase to search for in past conversation content.
        days_back: How many days back to search (default 7, max 30).

    Returns:
        Matching conversation excerpts with timestamps.
    """
    with LLMObs.tool(name="search_conversation_history") as span:
        try:
            hours = min(days_back * 24, 720)  # cap at 30 days
            results = search_spans_by_keyword(keyword, hours_back=hours)

            if not results:
                return f"No past conversations mentioning '{keyword}' found in the last {days_back} days."

            lines = [f"Found {len(results)} result(s) mentioning '{keyword}':\n"]
            for r in results:
                lines.append(
                    f"[{r['start']}] ({r['kind']})\n"
                    f"  {r['input'][:150]}...\n"
                    f"  → {r['output'][:150]}...\n"
                )

            result = "\n".join(lines)
            LLMObs.annotate(span=span, input_data={"keyword": keyword, "days_back": days_back},
                            output_data={"found": len(results)})
            return result

        except Exception as e:
            return f"Error searching Datadog: {e}"
```

### `extract_memories_from_traces`

```python
@tool
def extract_memories_from_traces(hours_back: int = 48) -> str:
    """
    Scan recent Datadog traces and extract facts worth saving to long-term memory (Neo4j).
    Reads recent conversation spans, identifies memorable information, and saves them.
    Call this periodically or when the user asks you to "learn from recent chats".

    Args:
        hours_back: How far back to scan for conversations (default 48h).

    Returns:
        Summary of memories extracted and saved.
    """
    with LLMObs.tool(name="extract_memories_from_traces") as span:
        try:
            spans = fetch_recent_agent_spans(hours_back=hours_back, limit=50)
            if not spans:
                return "No recent conversations to extract memories from."

            # Build the extraction prompt
            conversation_text = "\n---\n".join([
                f"[{s['start']}]\nUser: {s['input']}\nAssistant: {s['output']}"
                for s in spans if s['input'] and s['output']
            ])

            extraction_prompt = f"""You are a memory extractor. Review these past conversations 
and identify facts worth saving to long-term memory. Focus on:
- User preferences (tools, languages, workflows)
- Facts about the user's environment (paths, servers, credentials hints)
- Project details (names, locations, tech stack)
- Completed tasks (what was built, deployed, configured)
- People mentioned

For each memory, output one line: SAVE | <category> | <fact> | <entities>
Categories: preference | fact | project | person | task | system

Conversations:
{conversation_text[:8000]}"""

            # Use the agent's model to extract — this itself gets traced
            # (In practice, call your model directly here)
            # The calling code in main.py should handle the actual LLM call

            result = f"Scanned {len(spans)} conversations from last {hours_back}h. Ready for extraction."
            LLMObs.annotate(span=span, input_data={"hours_back": hours_back, "spans_found": len(spans)},
                            output_data={"status": "ready"}, metadata={"prompt": extraction_prompt[:500]})
            return result

        except Exception as e:
            return f"Error extracting memories: {e}"
```

---

## 10. Span Query Syntax Reference

Use these in the `query` field of the Spans API filter, or in the Datadog Trace Explorer UI:

```
# Filter by service and span kind
service:clawdbot-agent @span.kind:agent

# Filter by span kind only
@span.kind:tool

# Filter by specific tool name
@span.kind:tool resource_name:run_shell_command

# Full-text search in span content
service:clawdbot-agent "docker"

# Filter by session tag
@meta.tags.session_id:abc123

# Filter by model provider tag
@meta.tags.model_provider:openai

# Errors only
status:error service:clawdbot-agent

# Combine filters
service:clawdbot-agent @span.kind:llm @meta.model:gpt-4o-mini

# Time-relative (in the UI, not in the API filter)
# Use from_/to_ ISO timestamps in the API
```

---

## 11. Credentials Checklist

Before wiring up the integration, confirm these are all set in `agent/.env`:

```env
# Required for sending traces
DD_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
DD_SITE=datadoghq.com
DD_LLMOBS_ENABLED=1
DD_LLMOBS_ML_APP=clawdbot
DD_LLMOBS_AGENTLESS_ENABLED=1

# Required for querying traces (the agent tools)
DD_APP_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxx

# Nice to have
DD_ENV=development
DD_SERVICE=clawdbot-agent
```

The `DD_APP_KEY` (Application Key) is what unlocks the read API — the API key alone only
lets you write. Go to **Organisation Settings → Application Keys → New Key**.

---

## 12. What to Build, In Order

### Phase 1 — Passive tracing (do this first)
- [ ] Install `ddtrace` and add `LLMObs.enable()` to `main.py`
- [ ] Add `DD_*` vars to `.env`
- [ ] Run the agent, send a message, verify the trace appears in Datadog's LLM Observability tab
- [ ] Confirm you can see: input prompt, output, token count, latency

### Phase 2 — Manual span wrapping
- [ ] Add `with LLMObs.agent(...)` wrapper around the agent turn in `main.py`
- [ ] Add `with LLMObs.tool(...)` wrappers to each tool in `agent.py`
- [ ] Verify the trace tree shows `agent → llm + tool` hierarchy in Datadog

### Phase 3 — Agent query tools
- [ ] Add `query_recent_conversations` tool to `agent.py`
- [ ] Add `search_conversation_history` tool to `agent.py`
- [ ] Test in chat: "what did we talk about yesterday?" — should hit Datadog and return results

### Phase 4 — Memory mining loop
- [ ] Add `extract_memories_from_traces` tool
- [ ] Wire it to call `save_memory` (Neo4j) for each extracted fact
- [ ] Test end-to-end: tell the agent your server IP → restart agent → ask the agent to mine traces → verify IP appears in Neo4j

---

## 13. The Full Data Flow

```
[Chat turn]
    │
    ▼
LLMObs.agent() span opens
    │
    ├── LLM call → LLMObs auto-instruments → sends LLM span to Datadog
    ├── tool call → LLMObs.tool() → sends Tool span to Datadog  
    └── LLM call → sends second LLM span to Datadog
    │
    ▼
Span closes → full trace visible in Datadog UI
    │
    ▼
[Later — agent's memory mining turn]
    │
    ▼
query_recent_conversations()  ─→  Datadog Spans API (GET /api/v2/spans/events)
    │
    ▼
extract_memories_from_traces()  ─→  LLM extraction prompt
    │
    ▼
save_memory()  ─→  Neo4j AuraDB  (:Memory node)
    │
    ▼
Next conversation turn auto-recalls from Neo4j
```

Datadog is the **source of truth for raw history**. Neo4j is the **structured long-term memory**. They serve different purposes and work together.