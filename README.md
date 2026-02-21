# strandyclawbot

> A workspace-aware AI assistant built with Strands SDK, AG-UI, CopilotKit, Neo4j memory, and Datadog observability.

## Architecture

```mermaid
flowchart TB
  subgraph Presentation["Presentation Layer"]
    FE["Next.js UI"]
    CPK["CopilotKit Chat and CoAgent State"]
    CAN["Canvas and Skills Panels"]
    FE --> CPK
    FE --> CAN
  end

  subgraph Integration["Protocol and API Layer"]
    API_CPK["/api/copilotkit (Next.js)"]
    API_SK["/api/skills (Next.js)"]
    AWP["/awp (FastAPI + ag_ui_strands)"]
    SKEP["/skills (FastAPI)"]
    API_CPK --> AWP
    API_SK --> SKEP
  end

  subgraph AgentCore["Agent Runtime Layer"]
    ORCH["Strands Agent Orchestrator"]
    PROMPT["Prompt Builder\nworkspace context + skills summary + memory recall"]
    MODELS["Model Adapter\nOpenAI / MiniMax / Anthropic"]
    ORCH --> PROMPT
    ORCH --> MODELS
  end

  subgraph Tools["Tooling Layer"]
    SYS["System Tools\nshell, files, time, calculator"]
    WST["Workspace Tools\nprovision, heartbeat, markdown memory"]
    SKT["Skill Tools\nlist, read, create"]
    MEMT["Memory Tools\nsave, search, reindex"]
    DDT["Datadog Tools\nquery/search traces, extract memories"]
  end

  subgraph Data["State and Storage Layer"]
    WS["Workspace Filesystem\nworkspaces/{thread_id}"]
    CORE["Core MD Files\nAGENTS, SOUL, TOOLS, IDENTITY, USER, HEARTBEAT"]
    MDF["Memory MD Files\nMEMORY.md and memory/date.md"]
    SKDIR["Skill Files\nskills/name/SKILL.md"]
    NEO["Neo4j Memory Graph\nMemory + MemoryChunk + indexes"]
    DD["Datadog LLMObs and Logs"]
    WS --> CORE
    WS --> MDF
    WS --> SKDIR
  end

  Presentation --> Integration
  Integration --> AgentCore
  AgentCore --> Tools
  Tools --> Data
  AgentCore --> Data
```

## End-to-End Flow

1. User sends a message in CopilotKit chat.
2. Frontend calls `/api/copilotkit`, which forwards to FastAPI `/awp`.
3. Strands agent prepares workspace context + skills summary + memory context.
4. Agent calls model and tools as needed.
5. Tool results stream back through AG-UI to chat + canvas.
6. Memory is persisted to markdown files and Neo4j (hybrid retrieval ready).
7. Datadog captures traces for observability and recall tools.

## Core Components

- `Strands SDK`: agent orchestration and tool execution loop
- `AG-UI + ag_ui_strands`: protocol bridge between backend events and CopilotKit
- `CopilotKit`: chat UI, coagent state, and runtime integration
- `Neo4j`: long-term memory graph + chunk embeddings + hybrid search
- `Datadog`: LLMObs traces and history query tools
- `Workspace Runtime`: per-thread folders, core md files, local skills, heartbeat

## Tool Design

### System / File Tools
- `run_shell_command`
- `read_file`
- `write_file`
- `list_directory`
- `calculator`
- `get_current_time`

### Workspace / Skill Tools
- `provision_workspace`
- `list_skills`
- `read_skill`
- `create_skill`

### Memory Tools
- `remember_note`
- `memory_get`
- `memory_search`
- `save_memory`
- `search_memory`
- `memory_embedding_status`
- `reindex_memory_embeddings`

### Observability Tools
- `query_recent_conversations`
- `search_conversation_history`
- `extract_memories_from_traces`

## Project Structure

```text
version1/
├── agent/
│   ├── main.py
│   ├── agent.py
│   ├── memory.py
│   ├── workspace.py
│   ├── datadog_client.py
│   └── tests/
├── src/app/
│   ├── page.tsx
│   ├── components/CanvasPanel.tsx
│   ├── api/copilotkit/route.ts
│   └── api/skills/route.ts
├── demo_script.md
└── README.md
```

## Setup

### Frontend

```bash
npm install
cp .env.example .env.local
```

### Agent

```bash
cd agent
pip install -r requirements.txt
cp .env.example .env
```

### Run

```bash
# from version1/
npm run dev
```

- UI: `http://localhost:3000`
- Agent health: `http://127.0.0.1:8000/health`

## Model Configuration

In `agent/.env` set one provider:

```env
MODEL_PROVIDER=openai|minimax|anthropic
```

MiniMax example:

```env
MODEL_PROVIDER=minimax
MINIMAX_API_KEY=...
MINIMAX_BASE_URL=https://api.minimax.io/v1
MINIMAX_MODEL=MiniMax-M2.5
```
