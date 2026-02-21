import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

if "strands" not in sys.modules:
    strands_mod = types.ModuleType("strands")
    strands_mod.Agent = object
    strands_mod.tool = lambda fn: fn
    sys.modules["strands"] = strands_mod

if "strands.models.openai" not in sys.modules:
    strands_models_mod = types.ModuleType("strands.models")
    sys.modules["strands.models"] = strands_models_mod
    openai_mod = types.ModuleType("strands.models.openai")
    openai_mod.OpenAIModel = object
    sys.modules["strands.models.openai"] = openai_mod

import agent  # noqa: E402


class FakeStore:
    def __init__(self):
        self.saved = []
        self.search_payload = []

    def save(self, **kwargs):
        self.saved.append(kwargs)
        return "memory-123"

    def search(self, query, limit=5):
        self.search_payload.append({"query": query, "limit": limit})
        return [
            {
                "id": "m1",
                "category": "fact",
                "content": "Server IP is 192.168.1.10",
                "entities": ["server", "192.168.1.10"],
            },
            {
                "id": "m2",
                "category": "preference",
                "content": "User prefers type hints in Python",
                "entities": ["Python"],
            },
        ]

    def reindex_embeddings(self, force=True):
        return {"embeddings_enabled": True, "processed": 2, "chunks": 6, "skipped": False}

    def ensure_embedding_index_state(self, auto_reindex=False):
        return {"embeddings_enabled": True, "changed": False, "reindexed": False}


class Neo4jToolTests(unittest.TestCase):
    def test_save_memory_tool_calls_store(self):
        fake = FakeStore()
        with patch.object(agent, "_MEMORY_STORE", fake), patch.object(agent, "_MEMORY_STORE_ERROR", None):
            result = agent.save_memory(
                content="User prefers strict typing",
                category="preference",
                entities=["typing", "python"],
                topics=["preferences"],
            )

        self.assertIn("Saved memory [memory-123]", result)
        self.assertTrue(fake.saved)
        payload = fake.saved[0]
        self.assertEqual(payload["category"], "preference")
        self.assertEqual(payload["entities"], ["typing", "python"])
        self.assertEqual(payload["topics"], ["preferences"])

    def test_save_memory_tool_rejects_bad_category(self):
        with patch.object(agent, "_MEMORY_STORE", None), patch.object(agent, "_MEMORY_STORE_ERROR", None):
            result = agent.save_memory(content="x", category="random", entities=["x"])
        self.assertTrue(result.startswith("Error: invalid category"))

    def test_search_memory_tool_formats_results(self):
        fake = FakeStore()
        with patch.object(agent, "_MEMORY_STORE", fake), patch.object(agent, "_MEMORY_STORE_ERROR", None):
            result = agent.search_memory("server", limit=3)

        self.assertIn("Found 2 memories", result)
        self.assertIn("[fact] Server IP is 192.168.1.10", result)
        self.assertEqual(fake.search_payload[0], {"query": "server", "limit": 3})

    def test_build_memory_context_returns_empty_on_failure(self):
        class BrokenStore:
            def search(self, *_args, **_kwargs):
                raise RuntimeError("boom")

        with patch.object(agent, "_MEMORY_STORE", BrokenStore()), patch.object(agent, "_MEMORY_STORE_ERROR", None):
            self.assertEqual(agent.build_memory_context("anything"), "")

    def test_query_recent_conversations_formats_datadog_results(self):
        spans = [
            {"start": "2026-02-20T10:00:00Z", "input": "How to deploy?", "output": "Use docker compose."},
            {"start": "2026-02-20T09:00:00Z", "input": "Remember my pref", "output": "Saved."},
        ]
        with patch.object(agent, "fetch_recent_agent_spans", return_value=spans):
            result = agent.query_recent_conversations(hours_back=24, limit=2)
        self.assertIn("Found 2 conversation(s)", result)
        self.assertIn("How to deploy?", result)

    def test_search_conversation_history_no_keyword(self):
        result = agent.search_conversation_history("", days_back=7)
        self.assertEqual(result, "Error: keyword is required.")

    def test_extract_memories_from_traces_saves_to_memory_store(self):
        class SavingStore:
            def __init__(self):
                self.calls = []

            def save(self, **kwargs):
                self.calls.append(kwargs)
                return "id-1"

        store = SavingStore()
        spans = [
            {"input": "I prefer Python tooling", "output": "Noted preference."},
            {"input": "Server IP is 10.0.0.1", "output": "Will remember."},
        ]
        with (
            patch.object(agent, "_MEMORY_STORE", store),
            patch.object(agent, "_MEMORY_STORE_ERROR", None),
            patch.object(agent, "fetch_recent_agent_spans", return_value=spans),
        ):
            result = agent.extract_memories_from_traces(hours_back=24)

        self.assertIn("Saved", result)
        self.assertGreaterEqual(len(store.calls), 1)

    def test_memory_embedding_status_tool(self):
        fake = FakeStore()
        with patch.object(agent, "_MEMORY_STORE", fake), patch.object(agent, "_MEMORY_STORE_ERROR", None):
            result = agent.memory_embedding_status()
        self.assertIn("Embeddings enabled=True", result)
        self.assertIn("config_changed=False", result)

    def test_reindex_memory_embeddings_tool(self):
        fake = FakeStore()
        with patch.object(agent, "_MEMORY_STORE", fake), patch.object(agent, "_MEMORY_STORE_ERROR", None):
            result = agent.reindex_memory_embeddings(force=True)
        self.assertIn("processed=2", result)
        self.assertIn("chunks=6", result)


if __name__ == "__main__":
    unittest.main()
