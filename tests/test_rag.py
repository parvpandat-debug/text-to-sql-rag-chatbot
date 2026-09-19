"""
Unit Tests for RAG Module (Schema Chunking, Vector Storage, Retrieval, and Invalidation).
"""

import pytest
from unittest.mock import patch
from langchain_core.documents import Document
from src.rag import DeterministicEmbeddings, SchemaRAGIndex, get_embedding_model


def test_deterministic_embeddings():
    emb = DeterministicEmbeddings()
    vecs = emb.embed_documents(["hello world", "test query"])
    assert len(vecs) == 2
    assert len(vecs[0]) == 32 or len(vecs[0]) == 64
    assert isinstance(vecs[0][0], float)

    query_vec = emb.embed_query("hello world")
    assert query_vec == vecs[0]


def test_schema_hash_includes_model_name():
    index = SchemaRAGIndex()
    chunks = {"customers": "CREATE TABLE customers..."}

    index.model_name = "models/gemini-embedding-001"
    hash1 = index.compute_schema_hash(chunks)

    index.model_name = "models/gemini-embedding-2"
    hash2 = index.compute_schema_hash(chunks)

    assert hash1 != hash2, "Schema hash must change if embedding model changes!"


def test_schema_hash_computation():
    index = SchemaRAGIndex()
    chunks1 = {"customers": "CREATE TABLE customers...", "orders": "CREATE TABLE orders..."}
    chunks2 = {"customers": "CREATE TABLE customers...", "orders": "CREATE TABLE orders..."}
    chunks3 = {"customers": "CREATE TABLE customers...", "orders": "CREATE TABLE orders (id INT, modified INT)..."}

    hash1 = index.compute_schema_hash(chunks1)
    hash2 = index.compute_schema_hash(chunks2)
    hash3 = index.compute_schema_hash(chunks3)

    assert hash1 == hash2
    assert hash1 != hash3


def test_load_example_documents():
    index = SchemaRAGIndex()
    docs = index.build_example_documents("data/examples.json")
    assert len(docs) >= 15
    assert all("question" in d.metadata for d in docs)
    assert all("sql" in d.metadata for d in docs)


def test_rag_retrieval(tmp_path):
    persist_dir = str(tmp_path / "test_rag")
    index = SchemaRAGIndex(persist_directory=persist_dir)

    class MockDBUtil:
        def get_usable_table_names(self):
            return ["customers", "orders", "products"]

        def get_table_info(self, tables):
            tbl = tables[0]
            return f"CREATE TABLE {tbl} (id INT PRIMARY KEY, name VARCHAR(50));"

    index.initialize_or_refresh_index(MockDBUtil(), force_rebuild=True)

    schema_ctx, examples_ctx, tables = index.retrieve_relevant_context(
        "Find customer orders",
        top_k_tables=2,
        top_k_examples=2
    )

    assert len(schema_ctx) > 0
    assert len(examples_ctx) > 0
    assert len(tables) > 0


def test_google_api_key_uses_gemini_embedding_model(monkeypatch):
    from src.config import settings
    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "test_fake_api_key_value")
    emb, model_name = get_embedding_model()
    assert model_name == "models/gemini-embedding-001"
    assert emb.__class__.__name__ == "GoogleGenerativeAIEmbeddings"


def test_rag_collection_drop_before_rebuild(tmp_path, monkeypatch):
    """Proves Chroma collections are deleted prior to rebuilding index."""
    import chromadb.api.client

    deleted_cols = []
    original_delete = chromadb.api.client.Client.delete_collection

    def mock_delete(self, name):
        deleted_cols.append(name)
        try:
            return original_delete(self, name)
        except Exception:
            pass

    monkeypatch.setattr(chromadb.api.client.Client, "delete_collection", mock_delete)

    persist_dir = str(tmp_path / "test_drop")
    index = SchemaRAGIndex(persist_directory=persist_dir)

    class MockDBUtil:
        def get_usable_table_names(self):
            return ["customers"]
        def get_table_info(self, tables):
            return "CREATE TABLE customers (id INT);"

    index.initialize_or_refresh_index(MockDBUtil(), force_rebuild=True)

    assert "table_schema_collection" in deleted_cols
    assert "few_shot_examples_collection" in deleted_cols


def test_rag_loud_failure_when_google_api_key_set_and_indexing_fails(tmp_path, monkeypatch):
    """Proves startup fails loudly if GOOGLE_API_KEY is configured but indexing fails."""
    from src.config import settings
    from langchain_chroma import Chroma

    monkeypatch.setattr(settings, "GOOGLE_API_KEY", "fake_gemini_key")
    persist_dir = str(tmp_path / "test_loud_fail")
    index = SchemaRAGIndex(persist_directory=persist_dir)

    def failing_from_documents(*args, **kwargs):
        raise RuntimeError("Google Generative AI Embedding API quota exhausted")

    monkeypatch.setattr(Chroma, "from_documents", failing_from_documents)

    class MockDBUtil:
        def get_usable_table_names(self):
            return ["customers"]
        def get_table_info(self, tables):
            return "CREATE TABLE customers (id INT);"

    with pytest.raises(RuntimeError) as exc_info:
        index.initialize_or_refresh_index(MockDBUtil(), force_rebuild=True)

    assert "Embedding API quota exhausted" in str(exc_info.value)

