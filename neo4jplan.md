# ClawdBot × Neo4j AuraDB — Memory Layer Plan

> Everything you need to set up Neo4j cloud (AuraDB Free) and wire it into the Strands agent for persistent memory.

---

## 1. What We're Building

```
User message
    │
    ▼
[Auto-Recall]  ──→  Neo4j AuraDB  ──→  Top-N relevant memories
    │                                        │
    ▼                                        ▼
Strands ReAct Agent  ◄──── injected as context into system prompt
    │
    ├── save_memory tool  ──→  Neo4j AuraDB  (writes new Memory node)
    └── search_memory tool ──→  Neo4j AuraDB  (ad-hoc deeper search)
```

Two flows, always running together:
- **Proactive**: Before every agent turn, auto-search Neo4j and prepend relevant memories to the prompt
- **Reactive**: Agent explicitly calls `save_memory` / `search_memory` tools during reasoning

---

## 2. Neo4j AuraDB Setup (Free Tier)

### Step 1 — Create your free instance

1. Go to **[console.neo4j.io](https://console.neo4j.io)**
2. Sign up / log in (Google or email)
3. Click **"New Instance"** → choose **AuraDB Free**
4. Region: pick closest to you
5. Name it: `clawdbot-memory`
6. Click **Create** — takes ~2 minutes to provision

> AuraDB Free gives you **200k nodes, 400k relationships, 1 GB storage** — more than enough for personal assistant memory.

### Step 2 — Save your credentials

When the instance is created, Neo4j shows you the credentials **once**. Save them immediately:

```
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=<generated-password>
```

Download the `.env` snippet or copy it somewhere safe — the password is not shown again.

### Step 3 — Add credentials to agent `.env`

Open `agent/.env` and add:

```env
# ─── Neo4j AuraDB ─────────────────────────────────────────────────────────────
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your-password-here
```

### Step 4 — Install the Python driver

```bash
pip install neo4j
```

Add to `agent/requirements.txt`:
```
neo4j>=5.20.0
```

### Step 5 — Verify connection

Run this one-liner to confirm everything works before touching the agent:

```python
from neo4j import GraphDatabase
import os

driver = GraphDatabase.driver(
    os.environ["NEO4J_URI"],
    auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"])
)
driver.verify_connectivity()
print("✓ Connected to Neo4j AuraDB")
driver.close()
```

---

## 3. Graph Schema Design

### Nodes

```
(:Memory {
    id:           string   -- UUID, primary key
    content:      string   -- the actual memory text
    category:     string   -- "preference" | "fact" | "task" | "project" | "person" | "system"
    source:       string   -- "agent_tool" | "auto_extract"
    created_at:   datetime
    last_accessed: datetime
    access_count: integer
})

(:Entity {
    name: string           -- e.g. "Python", "~/dev/clawdbot", "Alex"
    type: string           -- "person" | "technology" | "path" | "concept" | "place"
})

(:Topic {
    name: string           -- e.g. "infrastructure", "preferences", "projects"
})

(:Session {
    id:         string
    started_at: datetime
})
```

### Relationships

```
(:Memory)-[:MENTIONS]────→(:Entity)      -- memory references this entity
(:Memory)-[:TAGGED]─────→(:Topic)       -- memory belongs to this topic
(:Memory)-[:FROM_SESSION]→(:Session)    -- memory was created in this session
(:Memory)-[:RELATED_TO]─→(:Memory)      -- memories that are about the same thing
```

### Why this structure?

When you search for memories about "the server", you can:
1. Find memories containing "server" directly (text search)
2. Find the `Entity {name: "192.168.1.10"}` and traverse back to ALL memories that mention it — even ones that don't say "server"

That graph traversal is impossible in a flat key-value or SQL store.

---

## 4. Cypher Queries We'll Use

### Schema setup (run once on first boot)

```cypher
-- Full-text search index on memory content
CREATE FULLTEXT INDEX memory_content IF NOT EXISTS
FOR (m:Memory) ON EACH [m.content];

-- Unique constraint on Memory id
CREATE CONSTRAINT memory_id IF NOT EXISTS
FOR (m:Memory) REQUIRE m.id IS UNIQUE;

-- Unique constraint on Entity name+type
CREATE CONSTRAINT entity_unique IF NOT EXISTS
FOR (e:Entity) REQUIRE (e.name, e.type) IS UNIQUE;
```

### Save a memory

```cypher
MERGE (m:Memory {id: $id})
SET m.content      = $content,
    m.category     = $category,
    m.source       = $source,
    m.created_at   = datetime(),
    m.last_accessed = datetime(),
    m.access_count = 1

WITH m
UNWIND $entities AS ent
  MERGE (e:Entity {name: ent.name, type: ent.type})
  MERGE (m)-[:MENTIONS]->(e)

WITH m
UNWIND $topics AS topic
  MERGE (t:Topic {name: topic})
  MERGE (m)-[:TAGGED]->(t)

RETURN m.id AS id
```

### Search memories (full-text)

```cypher
CALL db.index.fulltext.queryNodes("memory_content", $query)
YIELD node AS m, score
WHERE score > 0.3
CALL {
  WITH m
  MATCH (m)-[:MENTIONS]->(e:Entity)
  RETURN collect(e.name) AS entities
}
RETURN m.id, m.content, m.category, m.created_at, score, entities
ORDER BY score DESC
LIMIT $limit
```

### Search by entity traversal

```cypher
-- Find all memories that mention the same entities as a known memory
MATCH (m:Memory {id: $memory_id})-[:MENTIONS]->(e:Entity)<-[:MENTIONS]-(related:Memory)
WHERE related.id <> $memory_id
RETURN related.content, related.category, count(e) AS shared_entities
ORDER BY shared_entities DESC
LIMIT 10
```

### Update last_accessed on recall

```cypher
MATCH (m:Memory {id: $id})
SET m.last_accessed = datetime(),
    m.access_count  = m.access_count + 1
```

### Get recent memories (for session continuity)

```cypher
MATCH (m:Memory)
RETURN m.content, m.category, m.created_at
ORDER BY m.created_at DESC
LIMIT 20
```

---

## 5. Memory Categories

Use these consistently so searches and filters are meaningful:

| Category | What goes in it | Example |
|---|---|---|
| `preference` | How the user likes things done | "User prefers type hints in Python" |
| `fact` | Factual info about user's world | "Server IP is 192.168.1.10" |
| `project` | Active codebases and work | "ClawdBot lives at ~/dev/clawdbot" |
| `person` | People the user mentions | "Alex is the user's co-founder" |
| `task` | Completed or ongoing tasks | "Deployed app to prod on 2026-02-20" |
| `system` | Machine/environment info | "User is on macOS 15, M3 chip" |

---

## 6. Agent Integration Points

### Where memory code lives

```
agent/
├── agent.py          ← add save_memory + search_memory tools here
├── memory.py         ← NEW: Neo4j driver, schema setup, all Cypher queries
└── main.py           ← add auto-recall hook before each agent turn
```

### `memory.py` — what it will contain

```python
class MemoryStore:
    def __init__(self):          # connects to AuraDB, runs schema setup
    def save(self, content, category, entities, topics, session_id)
    def search(self, query, limit=5) -> list[dict]
    def recent(self, limit=10)  -> list[dict]
    def close(self)
```

### `agent.py` — two new Strands tools

```python
@tool
def save_memory(content: str, category: str, entities: list[str]) -> str:
    """
    Save an important fact or piece of information to long-term memory.
    Call this when the user shares something worth remembering across sessions:
    preferences, facts about their environment, project details, people, etc.
    
    Args:
        content:  The memory to save, written as a clear self-contained sentence.
        category: One of: preference | fact | project | person | task | system
        entities: Key nouns to extract for graph linking, e.g. ["Python", "~/dev/clawdbot"]
    """
    ...

@tool  
def search_memory(query: str, limit: int = 5) -> str:
    """
    Search long-term memory for information relevant to a query.
    Use this when you need to recall something the user told you before,
    or when context from past sessions would help answer the current question.
    
    Args:
        query: Natural language search query
        limit: Max results to return (default 5)
    """
    ...
```

### `main.py` — auto-recall hook

Before calling the agent, run a search and inject results into the system prompt:

```python
async def run_with_memory(user_message: str, agent: Agent):
    # 1. Search memory for this message
    memories = memory_store.search(user_message, limit=5)
    
    # 2. Format as context block
    if memories:
        memory_context = "Relevant memories from past sessions:\n"
        for m in memories:
            memory_context += f"- [{m['category']}] {m['content']}\n"
    else:
        memory_context = ""
    
    # 3. Prepend to system prompt
    agent.system_prompt = BASE_SYSTEM_PROMPT + "\n\n" + memory_context
    
    # 4. Run agent normally
    return agent(user_message)
```

---

## 7. What to Build, In Order

### Phase 1 — Connection & Schema ✅ Do this first
- [ ] Create AuraDB Free instance
- [ ] Save credentials to `agent/.env`
- [ ] Write `memory.py` with driver + schema setup + `save()` + `search()`
- [ ] Test with a standalone script: save 3 memories, search them, verify in AuraDB console

### Phase 2 — Agent Tools
- [ ] Add `save_memory` tool to `agent.py`
- [ ] Add `search_memory` tool to `agent.py`
- [ ] Test in chat: ask agent to remember something, verify it appears in Neo4j browser

### Phase 3 — Auto-Recall
- [ ] Add pre-turn memory injection in `main.py`
- [ ] Test cross-session: restart agent, ask about something saved in a previous session

### Phase 4 — (Future) Vector Search
- [ ] Generate embeddings with `text-embedding-3-small` (OpenAI) or local model
- [ ] Store as `m.embedding` on Memory nodes
- [ ] Use `db.index.vector.queryNodes` for semantic search alongside full-text

---

## 8. Viewing Your Data

Once memories are being saved, use the **Neo4j AuraDB Browser** (built into the console) to inspect them:

```cypher
-- See all memories
MATCH (m:Memory) RETURN m ORDER BY m.created_at DESC LIMIT 50

-- See the full graph
MATCH (m:Memory)-[r]->(n) RETURN m, r, n LIMIT 100

-- Count by category
MATCH (m:Memory) RETURN m.category, count(*) ORDER BY count(*) DESC

-- Find memories about a specific entity
MATCH (e:Entity {name: "Python"})<-[:MENTIONS]-(m:Memory)
RETURN m.content, m.created_at
```

---

## 9. Quick Reference — Credentials Checklist

Before starting implementation, confirm you have these in `agent/.env`:

```env
# Neo4j AuraDB
NEO4J_URI=neo4j+s://xxxxxxxx.databases.neo4j.io   ✓ from AuraDB console
NEO4J_USERNAME=neo4j                                ✓ always "neo4j" on AuraDB
NEO4J_PASSWORD=xxxxxxxxxxxx                         ✓ from instance creation screen

# Your existing model key (at least one)
MODEL_PROVIDER=openai
OPENAI_API_KEY=sk-...
```

That's all that's needed from the Neo4j side. No local Docker, no extra config — AuraDB Free handles everything else.