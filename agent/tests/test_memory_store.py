import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from memory import MemoryStore


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def single(self):
        return self._rows[0] if self._rows else None


class FakeSession:
    def __init__(self):
        self.calls = []
        self.embedding_fingerprint = ""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def run(self, statement, **params):
        q = " ".join(statement.split())
        self.calls.append({"query": q, "params": params})

        if "MERGE (m:Memory {id: $id})" in q and "RETURN m.id AS id" in q:
            return FakeResult([{"id": params.get("id", "generated-id")}])

        if "MATCH (cfg:MemoryConfig {id: 'embedding'}) RETURN cfg.fingerprint AS fingerprint" in q:
            return FakeResult([{"fingerprint": self.embedding_fingerprint}] if self.embedding_fingerprint else [])

        if "MERGE (cfg:MemoryConfig {id: 'embedding'})" in q:
            self.embedding_fingerprint = str(params.get("fingerprint", ""))
            return FakeResult([])

        if 'CALL db.index.fulltext.queryNodes("memory_content", $search_query)' in q:
            return FakeResult(
                [
                    {
                        "id": "m1",
                        "content": "Server IP is 192.168.1.10",
                        "category": "fact",
                        "created_at": "2026-01-01",
                        "score": 1.2,
                        "entities": ["server", "192.168.1.10"],
                    }
                ]
            )

        if 'CALL db.index.vector.queryNodes("memory_chunk_embedding", $k, $embedding)' in q:
            return FakeResult(
                [
                    {
                        "id": "m1",
                        "content": "Server IP is 192.168.1.10",
                        "category": "fact",
                        "created_at": "2026-01-01",
                        "vector_score": 0.92,
                        "entities": ["server"],
                    },
                    {
                        "id": "m3",
                        "content": "Use ubuntu for deployment",
                        "category": "project",
                        "created_at": "2026-01-03",
                        "vector_score": 0.90,
                        "entities": ["ubuntu"],
                    },
                ]
            )

        if "MATCH (m:Memory) RETURN m.id AS id, m.content AS content, m.source AS source" in q:
            return FakeResult(
                [
                    {"id": "m1", "content": "Server IP is 192.168.1.10", "source": "agent_tool"},
                    {"id": "m2", "content": "User prefers type hints", "source": "agent_tool"},
                ]
            )

        if "OPTIONAL MATCH (m)-[:HAS_CHUNK]->(old:MemoryChunk)" in q:
            return FakeResult([])

        if "ORDER BY m.created_at DESC" in q:
            return FakeResult(
                [
                    {
                        "id": "m2",
                        "content": "User prefers type hints",
                        "category": "preference",
                        "created_at": "2026-01-02",
                    }
                ]
            )

        return FakeResult([])

    def execute_write(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)

    def execute_read(self, fn, *args, **kwargs):
        return fn(self, *args, **kwargs)


class FakeDriver:
    def __init__(self):
        self.sessions = []

    def session(self, database=None):
        session = FakeSession()
        self.sessions.append(session)
        return session

    def close(self):
        return None


def _all_calls(driver):
    return [call for session in driver.sessions for call in session.calls]


class MemoryStoreTests(unittest.TestCase):
    def test_memory_store_save_and_schema_queries(self):
        driver = FakeDriver()
        store = MemoryStore(driver=driver, database="neo4j")

        memory_id = store.save(
            content="User prefers type hints",
            category="preference",
            entities=["Python", {"name": "Alex", "type": "person"}],
            topics=["preferences", "preferences", "coding"],
            source="agent_tool",
            memory_id="mem-fixed",
        )

        self.assertEqual(memory_id, "mem-fixed")
        calls = _all_calls(driver)
        self.assertTrue(any("CREATE FULLTEXT INDEX memory_content" in c["query"] for c in calls))

        save_call = next(c for c in calls if "MERGE (m:Memory {id: $id})" in c["query"])
        self.assertEqual(
            save_call["params"]["entities"],
            [
                {"name": "Python", "type": "concept"},
                {"name": "Alex", "type": "person"},
            ],
        )
        self.assertEqual(save_call["params"]["topics"], ["preferences", "coding"])

    def test_memory_store_search_updates_access_count(self):
        driver = FakeDriver()
        store = MemoryStore(driver=driver, database="neo4j")

        results = store.search("server", limit=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["id"], "m1")

        calls = _all_calls(driver)
        touch_calls = [c for c in calls if "SET m.last_accessed = datetime()" in c["query"]]
        self.assertTrue(touch_calls)
        self.assertEqual(touch_calls[0]["params"]["id"], "m1")

    def test_memory_store_recent_returns_latest_rows(self):
        driver = FakeDriver()
        store = MemoryStore(driver=driver, database="neo4j")

        rows = store.recent(limit=10)

        self.assertEqual(
            rows,
            [
                {
                    "id": "m2",
                    "content": "User prefers type hints",
                    "category": "preference",
                    "created_at": "2026-01-02",
                }
            ],
        )

    def test_memory_store_hybrid_search_merges_keyword_and_vector(self):
        driver = FakeDriver()
        with patch.dict(
            "os.environ",
            {
                "MEMORY_EMBEDDINGS_ENABLED": "1",
                "MEMORY_EMBEDDING_MODEL": "test-model",
                "MEMORY_EMBEDDING_DIMENSIONS": "3",
            },
            clear=False,
        ):
            store = MemoryStore(
                driver=driver,
                database="neo4j",
                embedder=lambda texts, _model, _dims: [[0.1, 0.2, 0.3] for _ in texts],
            )
            results = store.search("server", limit=5)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0]["id"], "m1")
        self.assertIn("score", results[0])
        self.assertIn("vector_score", results[0])

    def test_reindex_embeddings_rebuilds_chunks(self):
        driver = FakeDriver()
        with patch.dict(
            "os.environ",
            {
                "MEMORY_EMBEDDINGS_ENABLED": "1",
                "MEMORY_EMBEDDING_MODEL": "test-model",
                "MEMORY_EMBEDDING_DIMENSIONS": "3",
                "MEMORY_CHUNK_SIZE": "20",
                "MEMORY_CHUNK_OVERLAP": "5",
            },
            clear=False,
        ):
            store = MemoryStore(
                driver=driver,
                database="neo4j",
                embedder=lambda texts, _model, _dims: [[0.1, 0.2, 0.3] for _ in texts],
            )
            out = store.reindex_embeddings(force=True)

        self.assertTrue(out["embeddings_enabled"])
        self.assertGreaterEqual(out["processed"], 1)
        calls = _all_calls(driver)
        self.assertTrue(any("OPTIONAL MATCH (m)-[:HAS_CHUNK]->(old:MemoryChunk)" in c["query"] for c in calls))


if __name__ == "__main__":
    unittest.main()
