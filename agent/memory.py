"""Neo4j-backed long-term memory store for ClawdBot."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from typing import Any

try:
    from neo4j import GraphDatabase
except Exception:  # pragma: no cover
    GraphDatabase = None

try:
    from openai import OpenAI
except Exception:  # pragma: no cover
    OpenAI = None


class MemoryStoreError(RuntimeError):
    """Raised when memory operations cannot be completed."""


class MemoryStore:
    """Persistent memory store backed by Neo4j AuraDB."""

    def __init__(
        self,
        uri: str | None = None,
        user: str | None = None,
        password: str | None = None,
        database: str | None = None,
        driver: Any | None = None,
        embedder: Any | None = None,
    ) -> None:
        self.database = database or os.getenv("NEO4J_DATABASE", "neo4j")
        self.embeddings_enabled = os.getenv("MEMORY_EMBEDDINGS_ENABLED", "0") == "1"
        self.embedding_model = os.getenv("MEMORY_EMBEDDING_MODEL", "text-embedding-3-small")
        self.embedding_dimensions = int(os.getenv("MEMORY_EMBEDDING_DIMENSIONS", "1536"))
        self.chunk_size = max(200, int(os.getenv("MEMORY_CHUNK_SIZE", "800")))
        self.chunk_overlap = max(0, int(os.getenv("MEMORY_CHUNK_OVERLAP", "120")))
        if self.chunk_overlap >= self.chunk_size:
            self.chunk_overlap = max(0, self.chunk_size // 4)
        self.keyword_weight = float(os.getenv("MEMORY_KEYWORD_WEIGHT", "0.45"))
        self.vector_weight = float(os.getenv("MEMORY_VECTOR_WEIGHT", "0.55"))
        self._embedder = embedder
        self._openai_client: Any | None = None

        if driver is not None:
            self.driver = driver
            self._ensure_schema()
            self.ensure_embedding_index_state(auto_reindex=False)
            return

        neo4j_uri = uri or os.getenv("NEO4J_URI")
        neo4j_user = user or os.getenv("NEO4J_USER") or os.getenv("NEO4J_USERNAME")
        neo4j_password = password or os.getenv("NEO4J_PASSWORD")

        if not neo4j_uri or not neo4j_user or not neo4j_password:
            raise MemoryStoreError(
                "Neo4j is not configured. Set NEO4J_URI, NEO4J_USER/NEO4J_USERNAME, and NEO4J_PASSWORD."
            )
        if GraphDatabase is None:
            raise MemoryStoreError("neo4j package is not installed. Run: pip install neo4j")

        self.driver = GraphDatabase.driver(neo4j_uri, auth=(neo4j_user, neo4j_password))
        self.driver.verify_connectivity()
        self._ensure_schema()
        self.ensure_embedding_index_state(auto_reindex=True)

    def close(self) -> None:
        self.driver.close()

    def _ensure_schema(self) -> None:
        queries = [
            "CREATE FULLTEXT INDEX memory_content IF NOT EXISTS FOR (m:Memory) ON EACH [m.content]",
            "CREATE CONSTRAINT memory_id IF NOT EXISTS FOR (m:Memory) REQUIRE m.id IS UNIQUE",
            "CREATE CONSTRAINT entity_unique IF NOT EXISTS FOR (e:Entity) REQUIRE (e.name, e.type) IS UNIQUE",
            "CREATE CONSTRAINT memory_chunk_id IF NOT EXISTS FOR (c:MemoryChunk) REQUIRE c.id IS UNIQUE",
        ]
        if self.embeddings_enabled:
            queries.append(
                "CREATE VECTOR INDEX memory_chunk_embedding IF NOT EXISTS "
                "FOR (c:MemoryChunk) ON (c.embedding) "
                f"OPTIONS {{indexConfig: {{`vector.dimensions`: {self.embedding_dimensions}, `vector.similarity_function`: 'cosine'}}}}"
            )
        with self.driver.session(database=self.database) as session:
            for query in queries:
                session.run(query)

    @staticmethod
    def _normalize_entities(entities: list[Any] | None) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for entity in entities or []:
            if isinstance(entity, dict):
                name = str(entity.get("name", "")).strip()
                entity_type = str(entity.get("type", "concept")).strip() or "concept"
            else:
                name = str(entity).strip()
                entity_type = "concept"
            if not name:
                continue
            normalized.append({"name": name, "type": entity_type})
        return normalized

    @staticmethod
    def _normalize_topics(topics: list[str] | None) -> list[str]:
        seen: set[str] = set()
        normalized: list[str] = []
        for topic in topics or []:
            value = str(topic).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            normalized.append(value)
        return normalized

    @staticmethod
    def _execute_write(session: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
        if hasattr(session, "execute_write"):
            return session.execute_write(fn, *args, **kwargs)
        return session.write_transaction(fn, *args, **kwargs)

    @staticmethod
    def _execute_read(session: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
        if hasattr(session, "execute_read"):
            return session.execute_read(fn, *args, **kwargs)
        return session.read_transaction(fn, *args, **kwargs)

    def _embedding_fingerprint(self) -> str:
        payload = {
            "enabled": self.embeddings_enabled,
            "model": self.embedding_model,
            "dimensions": self.embedding_dimensions,
            "chunk_size": self.chunk_size,
            "chunk_overlap": self.chunk_overlap,
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _split_chunks(self, text: str) -> list[str]:
        clean = (text or "").strip()
        if not clean:
            return []
        chunks: list[str] = []
        step = max(1, self.chunk_size - self.chunk_overlap)
        start = 0
        while start < len(clean):
            chunk = clean[start : start + self.chunk_size].strip()
            if chunk:
                chunks.append(chunk)
            start += step
        return chunks[:100]

    def _get_openai_client(self) -> Any:
        if self._openai_client is not None:
            return self._openai_client
        if OpenAI is None:
            raise MemoryStoreError("openai package is not installed. Run: pip install openai")
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise MemoryStoreError("OPENAI_API_KEY is required for embedding generation.")
        self._openai_client = OpenAI(api_key=api_key)
        return self._openai_client

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not self.embeddings_enabled:
            return []
        filtered = [t for t in texts if t.strip()]
        if not filtered:
            return []
        if self._embedder is not None:
            return self._embedder(filtered, self.embedding_model, self.embedding_dimensions)
        client = self._get_openai_client()
        response = client.embeddings.create(
            model=self.embedding_model,
            input=filtered,
            dimensions=self.embedding_dimensions,
        )
        return [list(map(float, row.embedding)) for row in response.data]

    def _replace_chunks(self, memory_id: str, content: str, source: str) -> int:
        chunks = self._split_chunks(content)
        vectors: list[list[float]] = []
        if self.embeddings_enabled and chunks:
            vectors = self._embed_texts(chunks)
            if len(vectors) != len(chunks):
                raise MemoryStoreError("Embedding count does not match chunk count.")

        payload_chunks = []
        for idx, chunk_text in enumerate(chunks):
            payload_chunks.append(
                {
                    "id": str(uuid.uuid4()),
                    "chunk_index": idx,
                    "content": chunk_text,
                    "source": source,
                    "embedding": vectors[idx] if vectors else None,
                }
            )

        query = """
        MATCH (m:Memory {id: $memory_id})
        OPTIONAL MATCH (m)-[:HAS_CHUNK]->(old:MemoryChunk)
        DETACH DELETE old
        WITH m, $chunks AS chunks
        UNWIND chunks AS chunk
          CREATE (c:MemoryChunk {
            id: chunk.id,
            chunk_index: chunk.chunk_index,
            content: chunk.content,
            source: chunk.source,
            created_at: datetime(),
            embedding: chunk.embedding
          })
          MERGE (m)-[:HAS_CHUNK]->(c)
        """

        def _tx(tx: Any) -> None:
            tx.run(query, memory_id=memory_id, chunks=payload_chunks)

        with self.driver.session(database=self.database) as session:
            self._execute_write(session, _tx)
        return len(payload_chunks)

    def ensure_embedding_index_state(self, auto_reindex: bool = True) -> dict[str, Any]:
        if not self.embeddings_enabled:
            return {"embeddings_enabled": False, "reindexed": False}

        desired = self._embedding_fingerprint()
        read_query = "MATCH (cfg:MemoryConfig {id: 'embedding'}) RETURN cfg.fingerprint AS fingerprint"
        upsert_query = """
        MERGE (cfg:MemoryConfig {id: 'embedding'})
        SET cfg.fingerprint = $fingerprint,
            cfg.updated_at = datetime()
        """

        def _read_tx(tx: Any) -> str:
            row = tx.run(read_query).single()
            if not row:
                return ""
            return str(row.get("fingerprint") or "")

        with self.driver.session(database=self.database) as session:
            current = self._execute_read(session, _read_tx)

        changed = current != desired
        reindexed = False
        if changed and auto_reindex:
            self.reindex_embeddings(force=True)
            reindexed = True

        if changed:
            with self.driver.session(database=self.database) as session:
                self._execute_write(session, lambda tx: tx.run(upsert_query, fingerprint=desired))

        return {"embeddings_enabled": True, "changed": changed, "reindexed": reindexed}

    def reindex_embeddings(self, force: bool = False) -> dict[str, Any]:
        if not self.embeddings_enabled:
            return {"embeddings_enabled": False, "processed": 0, "chunks": 0}

        if not force:
            state = self.ensure_embedding_index_state(auto_reindex=False)
            if not state.get("changed"):
                return {"embeddings_enabled": True, "processed": 0, "chunks": 0, "skipped": True}

        query = "MATCH (m:Memory) RETURN m.id AS id, m.content AS content, m.source AS source"

        def _tx(tx: Any) -> list[dict[str, str]]:
            rows = tx.run(query)
            return [{"id": str(r.get("id")), "content": str(r.get("content") or ""), "source": str(r.get("source") or "agent_tool")} for r in rows]

        with self.driver.session(database=self.database) as session:
            memories = self._execute_read(session, _tx)

        processed = 0
        total_chunks = 0
        for row in memories:
            if not row["id"]:
                continue
            total_chunks += self._replace_chunks(row["id"], row["content"], row["source"])
            processed += 1

        # Persist latest config fingerprint after successful rebuild.
        desired = self._embedding_fingerprint()
        upsert_query = """
        MERGE (cfg:MemoryConfig {id: 'embedding'})
        SET cfg.fingerprint = $fingerprint,
            cfg.updated_at = datetime()
        """
        with self.driver.session(database=self.database) as session:
            self._execute_write(session, lambda tx: tx.run(upsert_query, fingerprint=desired))

        return {"embeddings_enabled": True, "processed": processed, "chunks": total_chunks}

    def save(
        self,
        content: str,
        category: str,
        entities: list[Any] | None = None,
        topics: list[str] | None = None,
        source: str = "agent_tool",
        session_id: str | None = None,
        memory_id: str | None = None,
    ) -> str:
        content = (content or "").strip()
        category = (category or "").strip()
        if not content:
            raise MemoryStoreError("Memory content cannot be empty.")
        if not category:
            raise MemoryStoreError("Memory category cannot be empty.")

        payload = {
            "id": memory_id or str(uuid.uuid4()),
            "content": content,
            "category": category,
            "source": source,
            "entities": self._normalize_entities(entities),
            "topics": self._normalize_topics(topics),
            "session_id": session_id,
        }

        query = """
        MERGE (m:Memory {id: $id})
        SET m.content = $content,
            m.category = $category,
            m.source = $source,
            m.created_at = coalesce(m.created_at, datetime()),
            m.last_accessed = datetime(),
            m.access_count = coalesce(m.access_count, 0) + 1
        WITH m
        UNWIND $entities AS ent
          MERGE (e:Entity {name: ent.name, type: ent.type})
          MERGE (m)-[:MENTIONS]->(e)
        WITH m
        UNWIND $topics AS topic
          MERGE (t:Topic {name: topic})
          MERGE (m)-[:TAGGED]->(t)
        FOREACH (_ IN CASE WHEN $session_id IS NULL THEN [] ELSE [1] END |
          MERGE (s:Session {id: $session_id})
          ON CREATE SET s.started_at = datetime()
          MERGE (m)-[:FROM_SESSION]->(s)
        )
        RETURN m.id AS id
        """

        def _tx(tx: Any) -> str:
            record = tx.run(query, **payload).single()
            if not record:
                raise MemoryStoreError("Failed to save memory.")
            return str(record["id"])

        with self.driver.session(database=self.database) as session:
            memory_id_out = self._execute_write(session, _tx)

        if self.embeddings_enabled:
            self._replace_chunks(memory_id_out, content, source)
        return memory_id_out

    def _touch(self, memory_id: str) -> None:
        query = """
        MATCH (m:Memory {id: $id})
        SET m.last_accessed = datetime(),
            m.access_count = coalesce(m.access_count, 0) + 1
        """

        def _tx(tx: Any) -> None:
            tx.run(query, id=memory_id)

        with self.driver.session(database=self.database) as session:
            self._execute_write(session, _tx)

    def _fulltext_search(self, text_query: str, safe_limit: int) -> list[dict[str, Any]]:
        cypher = """
        CALL db.index.fulltext.queryNodes("memory_content", $search_query)
        YIELD node AS m, score
        WHERE score > 0.3
        CALL (m) {
          OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
          RETURN collect(e.name) AS entities
        }
        RETURN m.id AS id,
               m.content AS content,
               m.category AS category,
               m.created_at AS created_at,
               score,
               entities
        ORDER BY score DESC
        LIMIT $limit
        """

        def _tx(tx: Any) -> list[dict[str, Any]]:
            rows = tx.run(cypher, search_query=text_query, limit=safe_limit)
            return [
                {
                    "id": row.get("id"),
                    "content": row.get("content", ""),
                    "category": row.get("category", "unknown"),
                    "created_at": row.get("created_at"),
                    "keyword_score": float(row.get("score", 0.0)),
                    "entities": row.get("entities") or [],
                }
                for row in rows
            ]

        with self.driver.session(database=self.database) as session:
            return self._execute_read(session, _tx)

    def _vector_search(self, text_query: str, safe_limit: int) -> list[dict[str, Any]]:
        if not self.embeddings_enabled:
            return []
        vectors = self._embed_texts([text_query])
        if not vectors:
            return []
        embedding = vectors[0]
        cypher = """
        CALL db.index.vector.queryNodes("memory_chunk_embedding", $k, $embedding)
        YIELD node AS c, score
        MATCH (m:Memory)-[:HAS_CHUNK]->(c)
        CALL (m) {
          OPTIONAL MATCH (m)-[:MENTIONS]->(e:Entity)
          RETURN collect(e.name) AS entities
        }
        RETURN m.id AS id,
               m.content AS content,
               m.category AS category,
               m.created_at AS created_at,
               max(score) AS vector_score,
               entities
        ORDER BY vector_score DESC
        LIMIT $k
        """

        def _tx(tx: Any) -> list[dict[str, Any]]:
            rows = tx.run(cypher, k=safe_limit, embedding=embedding)
            return [
                {
                    "id": row.get("id"),
                    "content": row.get("content", ""),
                    "category": row.get("category", "unknown"),
                    "created_at": row.get("created_at"),
                    "vector_score": float(row.get("vector_score", 0.0)),
                    "entities": row.get("entities") or [],
                }
                for row in rows
            ]

        with self.driver.session(database=self.database) as session:
            return self._execute_read(session, _tx)

    def _merge_search_results(
        self,
        keyword_rows: list[dict[str, Any]],
        vector_rows: list[dict[str, Any]],
        limit: int,
    ) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        max_kw = max([float(r.get("keyword_score", 0.0)) for r in keyword_rows], default=1.0) or 1.0
        max_vec = max([float(r.get("vector_score", 0.0)) for r in vector_rows], default=1.0) or 1.0

        for row in keyword_rows:
            memory_id = str(row.get("id") or "")
            if not memory_id:
                continue
            merged[memory_id] = dict(row)
            merged[memory_id]["keyword_score"] = float(row.get("keyword_score", 0.0))
            merged[memory_id]["vector_score"] = float(row.get("vector_score", 0.0))

        for row in vector_rows:
            memory_id = str(row.get("id") or "")
            if not memory_id:
                continue
            if memory_id not in merged:
                merged[memory_id] = dict(row)
                merged[memory_id]["keyword_score"] = float(row.get("keyword_score", 0.0))
                merged[memory_id]["vector_score"] = float(row.get("vector_score", 0.0))
            else:
                merged[memory_id]["vector_score"] = max(
                    float(merged[memory_id].get("vector_score", 0.0)),
                    float(row.get("vector_score", 0.0)),
                )
                if not merged[memory_id].get("entities"):
                    merged[memory_id]["entities"] = row.get("entities") or []

        rows = list(merged.values())
        for row in rows:
            kw_norm = float(row.get("keyword_score", 0.0)) / max_kw
            vec_norm = float(row.get("vector_score", 0.0)) / max_vec
            row["score"] = (self.keyword_weight * kw_norm) + (self.vector_weight * vec_norm)

        rows.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
        return rows[:limit]

    def search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        text_query = (query or "").strip()
        if not text_query:
            return []
        safe_limit = max(1, min(int(limit), 25))
        keyword_rows = self._fulltext_search(text_query, safe_limit * 3)
        vector_rows: list[dict[str, Any]] = []
        if self.embeddings_enabled:
            try:
                vector_rows = self._vector_search(text_query, safe_limit * 3)
            except Exception:
                vector_rows = []
        results = self._merge_search_results(keyword_rows, vector_rows, safe_limit)

        for memory in results:
            memory_id = memory.get("id")
            if memory_id:
                self._touch(str(memory_id))

        return results

    def recent(self, limit: int = 10) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 50))
        cypher = """
        MATCH (m:Memory)
        RETURN m.id AS id,
               m.content AS content,
               m.category AS category,
               m.created_at AS created_at
        ORDER BY m.created_at DESC
        LIMIT $limit
        """

        def _tx(tx: Any) -> list[dict[str, Any]]:
            rows = tx.run(cypher, limit=safe_limit)
            return [
                {
                    "id": row.get("id"),
                    "content": row.get("content", ""),
                    "category": row.get("category", "unknown"),
                    "created_at": row.get("created_at"),
                }
                for row in rows
            ]

        with self.driver.session(database=self.database) as session:
            return self._execute_read(session, _tx)
