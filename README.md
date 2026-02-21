# 🦞 ClawdBot

> A personal AI assistant with access to your machine — built with [Strands Agents](https://strandsagents.com) + [CopilotKit](https://copilotkit.ai).

Chat with your AI from a sleek UI. It can run shell commands, read/write files, do math, and more. Open the **Canvas panel** to inspect code and outputs side-by-side.

---

## Architecture

```
Browser (Next.js)
  └─ CopilotKit sidebar (AG-UI protocol)
       └─ /api/copilotkit  (Next.js route)
            └─ CopilotRuntime + HttpAgent
                 └─ Python FastAPI  :8000/awp
                      └─ Strands ReAct Agent
                           └─ LLM (OpenAI / MiniMax / Anthropic)
                           └─ Tools: shell, files, calculator, time
```

---

## Prerequisites

- Node.js ≥ 20
- Python ≥ 3.12
- At least one API key (OpenAI, MiniMax, or Anthropic)

---

## Setup

### 1. Frontend

```bash
npm install
cp .env.example .env.local
```

### 2. Agent

```bash
cd agent
pip install -r requirements.txt
cp .env.example .env
# Edit .env and add your API key
```

### 3. Run everything

```bash
# From project root — starts both UI and agent concurrently
npm run dev
```

- UI → http://localhost:3000
- Agent → http://localhost:8000
- Health check → http://localhost:8000/health

---

## Model Providers

Set `MODEL_PROVIDER` in `agent/.env`:

| Provider | `MODEL_PROVIDER` | Required env var |
|----------|-----------------|-----------------|
| OpenAI   | `openai` (default) | `OPENAI_API_KEY` |
| MiniMax  | `minimax`       | `MINIMAX_API_KEY` |
| Anthropic | `anthropic`    | `ANTHROPIC_API_KEY` |

### OpenAI
```env
MODEL_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini   # or gpt-4o, gpt-4-turbo
```

### MiniMax
```env
MODEL_PROVIDER=minimax
MINIMAX_API_KEY=your-key
MINIMAX_MODEL=MiniMax-Text-01
MINIMAX_BASE_URL=https://api.minimax.chat/v1
```

### Anthropic
```env
MODEL_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-3-5-haiku-20241022
```

---

## Features

### Chat UI
- Persistent sidebar chat powered by CopilotKit
- Streaming responses
- Agent state awareness

### Canvas Panel
- Toggle with the **Canvas** button (top-right) or `⌘K`
- Displays code, file contents, and command output from agent state
- Tab view when multiple items exist
- One-click copy

### Agent Tools
| Tool | Description |
|------|-------------|
| `get_current_time` | Returns current date/time |
| `calculator` | Safe math evaluation (supports `math.*` functions) |
| `run_shell_command` | Executes shell commands (30s timeout) |
| `read_file` | Reads local file contents |
| `write_file` | Writes/creates local files |
| `list_directory` | Lists files and folders |

---

## Project Structure

```
clawdbot/
├── agent/
│   ├── agent.py          # Strands agent, tools, model factory
│   ├── main.py           # FastAPI server + AG-UI endpoint
│   ├── requirements.txt
│   └── .env.example
├── src/
│   └── app/
│       ├── api/copilotkit/route.ts   # CopilotKit runtime bridge
│       ├── components/
│       │   ├── Header.tsx            # Top bar with canvas toggle
│       │   └── CanvasPanel.tsx       # Side canvas with agent state
│       ├── layout.tsx                # CopilotKit provider
│       ├── page.tsx                  # Main page
│       └── globals.css               # Dark theme + CopilotKit overrides
├── package.json
└── README.md
```

---

## Roadmap (OpenClaw-style)

- [ ] Persistent memory (file-based or vector store)
- [ ] Self-healing: agent detects errors and retries with corrected code
- [ ] Multi-agent routing
- [ ] Heartbeats / proactive tasks (cron)
- [ ] Messaging platform adapters (Telegram, WhatsApp)
- [ ] Skills system (pluggable tool packages)
