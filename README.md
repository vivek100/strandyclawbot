# strandyclawbot

> A workspace-aware AI assistant built with Strands SDK, AG-UI, CopilotKit, Neo4j memory, and Datadog observability.

## Architecture

```mermaid
flowchart LR
  U[User] --> FE[Next.js Frontend]
  FE --> CK[CopilotKit UI + Runtime]
  CK --> API1[/api/copilotkit]
  CK --> API2[/api/skills]

  API1 --> AWP[FastAPI /awp endpoint]
  API2 --> SK[FastAPI /skills endpoint]

  AWP --> AG[Strands Agent Orchestrator]
  AG --> MOD[Model Router\nOpenAI | MiniMax | Anthropic]

  AG --> TOOLS[Tool Layer]
  TOOLS --> SH[run_shell_command\nPowerShell/cmd/curl]
  TOOLS --> FS[read_file/write_file/list_directory]
  TOOLS --> MEMTOOLS[save/search/reindex memory]
  TOOLS --> SKTOOLS[list/read/create skill]
  TOOLS --> DDTOOLS[query/search Datadog traces]

  AG --> WS[Workspace Runtime\nworkspaces/<thread_id>]
  WS --> CORE[AGENTS.md / SOUL.md / TOOLS.md\nIDENTITY.md / USER.md / HEARTBEAT.md]
  WS --> MM[MEMORY.md + memory/YYYY-MM-DD.md]
  WS --> LSK[skills/*/SKILL.md]

  AG --> MEM[Neo4j Memory Store]
  MEM --> M1[(Memory nodes)]
  MEM --> M2[(MemoryChunk + embeddings)]
  MEM --> IDX[Fulltext + Vector index]

  AG --> DD[Datadog LLMObs + trace APIs]
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

