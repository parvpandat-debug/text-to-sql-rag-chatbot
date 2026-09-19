"""
Retrieval-Augmented Generation (RAG) Module for Dynamic Schema and Few-Shot Examples.

Features:
1. Dynamic schema chunking (one document per business table with DDL, descriptions, sample rows).
2. Few-shot demonstration index from data/examples.json.
3. Persistent vector store using ChromaDB and Google Gemini embeddings (with fallback for offline/test mode).
4. Automatic hash-based cache invalidation: rebuilds index if introspected database schema changes.
5. Contextual retrieval of top-k tables and top-k few-shot examples per question.
"""

import os
import json
import hashlib
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_chroma import Chroma

from src.config import settings

logger = logging.getLogger(__name__)


class DeterministicEmbeddings(Embeddings):
    """
    Offline/Hermetic Embedding fallback using deterministic cryptographic hashing.
    Enables instant offline testing and dev without requiring external Gemini API calls.
    """
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        results = []
        for text in texts:
            # Produce a 64-dimensional float vector between -1.0 and 1.0
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vec = [((b / 127.5) - 1.0) for b in digest]
            results.append(vec)
        return results

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


def get_embedding_model() -> Tuple[Embeddings, str]:
    """
    Returns Google Gemini embedding model if API key is configured, else fallback embedder.
    Fails loudly if GOOGLE_API_KEY is configured but invalid/failing.
    """
    api_key = settings.GOOGLE_API_KEY
    if api_key and api_key.strip():
        # Using current active Gemini embedding model (replaces shut-down text-embedding-004)
        from langchain_google_genai import GoogleGenerativeAIEmbeddings
        model_name = "models/gemini-embedding-001"
        return GoogleGenerativeAIEmbeddings(
            model=model_name,
            google_api_key=api_key.strip()
        ), model_name
    return DeterministicEmbeddings(), "deterministic-embeddings-v1"


class SchemaRAGIndex:
    """Manages persistent vector collections for schema chunks and few-shot examples."""

    def __init__(self, persist_directory: Optional[str] = None):
        self.persist_dir = persist_directory or settings.CHROMA_PERSIST_DIR
        self.embeddings, self.model_name = get_embedding_model()
        self.schema_collection_name = "table_schema_collection"
        self.examples_collection_name = "few_shot_examples_collection"

        self._schema_store: Optional[Chroma] = None
        self._examples_store: Optional[Chroma] = None

    def compute_schema_hash(self, table_chunks: Dict[str, str]) -> str:
        """Computes SHA-256 hash across all table definitions and embedding model to detect schema or model drift."""
        hasher = hashlib.sha256()
        hasher.update(self.model_name.encode("utf-8"))
        for tbl in sorted(table_chunks.keys()):
            hasher.update(tbl.encode("utf-8"))
            hasher.update(table_chunks[tbl].encode("utf-8"))
        return hasher.hexdigest()

    def _get_stored_schema_hash(self) -> Optional[str]:
        hash_file = Path(self.persist_dir) / "schema_hash.txt"
        if hash_file.is_file():
            return hash_file.read_text().strip()
        return None

    def _save_schema_hash(self, schema_hash: str) -> None:
        os.makedirs(self.persist_dir, exist_ok=True)
        hash_file = Path(self.persist_dir) / "schema_hash.txt"
        hash_file.write_text(schema_hash)

    def build_table_documents(self, db_util) -> List[Document]:
        """
        Chunks the introspected database schema into per-table documents
        containing DDL, column types, and sample data.
        """
        table_names = db_util.get_usable_table_names()
        documents = []

        for tbl in table_names:
            table_info = db_util.get_table_info([tbl])
            doc = Document(
                page_content=table_info.strip(),
                metadata={"table_name": tbl.lower()}
            )
            documents.append(doc)

        return documents

    def build_example_documents(self, examples_path: str = "data/examples.json") -> List[Document]:
        """Loads and formats few-shot demonstration pairs as vector documents."""
        if not os.path.exists(examples_path):
            return []

        try:
            with open(examples_path, "r", encoding="utf-8") as f:
                examples_data = json.load(f)
        except Exception as e:
            logger.error(f"Error loading {examples_path}: {e}")
            return []

        documents = []
        for item in examples_data:
            q = item.get("question", "")
            sql = item.get("sql", "")
            content = f"User Question: {q}\nGold SQL: {sql}"
            documents.append(
                Document(
                    page_content=content,
                    metadata={"question": q, "sql": sql}
                )
            )

        return documents

    def initialize_or_refresh_index(self, db_util, force_rebuild: bool = False) -> None:
        """
        Initializes persistent Chroma stores. Automatically deletes collections and rebuilds
        if schema hash changed or if force_rebuild is True.
        """
        table_docs = self.build_table_documents(db_util)
        table_chunks = {doc.metadata["table_name"]: doc.page_content for doc in table_docs}
        current_hash = self.compute_schema_hash(table_chunks)
        stored_hash = self._get_stored_schema_hash()

        needs_rebuild = force_rebuild or (stored_hash != current_hash)

        if needs_rebuild:
            logger.info("[RAG] Schema drift detected or initial index build required. Indexing schema & examples...")

            # Explicitly delete old Chroma collections before rebuilding
            try:
                import chromadb
                client = chromadb.PersistentClient(path=self.persist_dir)
                for col_name in [self.schema_collection_name, self.examples_collection_name]:
                    try:
                        client.delete_collection(col_name)
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Collection deletion check: {e}")

            # Index Table Schemas (fails loudly if embedding model or API fails)
            self._schema_store = Chroma.from_documents(
                documents=table_docs,
                embedding=self.embeddings,
                collection_name=self.schema_collection_name,
                persist_directory=self.persist_dir
            )

            # Index Few-shot Examples
            example_docs = self.build_example_documents()
            if example_docs:
                self._examples_store = Chroma.from_documents(
                    documents=example_docs,
                    embedding=self.embeddings,
                    collection_name=self.examples_collection_name,
                    persist_directory=self.persist_dir
                )

            self._save_schema_hash(current_hash)
            logger.info("[RAG] Vector index successfully built and persisted.")
        else:
            logger.info("[RAG] Vector index is up to date with introspected schema.")
            self._schema_store = Chroma(
                collection_name=self.schema_collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_dir
            )
            self._examples_store = Chroma(
                collection_name=self.examples_collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_dir
            )

    def retrieve_relevant_context(
        self,
        question: str,
        top_k_tables: int = 4,
        top_k_examples: int = 3
    ) -> Tuple[str, str, List[str]]:
        """
        Retrieves relevant table schemas and few-shot examples for the incoming question.

        Returns:
            Tuple of (formatted_schema_context, formatted_examples_context, list_of_retrieved_table_names)
        """
        retrieved_tables: List[str] = []
        schema_parts: List[str] = []
        examples_parts: List[str] = []

        # 1. Retrieve Table Schemas
        if self._schema_store:
            try:
                table_docs = self._schema_store.similarity_search(question, k=top_k_tables)
                for doc in table_docs:
                    tbl = doc.metadata.get("table_name")
                    if tbl and tbl not in retrieved_tables:
                        retrieved_tables.append(tbl)
                    schema_parts.append(doc.page_content)
            except Exception as e:
                logger.warning(f"Error querying schema store: {e}")

        # 2. Retrieve Few-Shot Examples
        if self._examples_store:
            try:
                example_docs = self._examples_store.similarity_search(question, k=top_k_examples)
                for idx, doc in enumerate(example_docs, 1):
                    q = doc.metadata.get("question")
                    s = doc.metadata.get("sql")
                    examples_parts.append(f"Example {idx}:\nQuestion: {q}\nSQL: {s}")
            except Exception as e:
                logger.warning(f"Error querying examples store: {e}")

        formatted_schema = "\n\n".join(schema_parts) if schema_parts else "No specific schema retrieved."
        formatted_examples = "\n\n".join(examples_parts) if examples_parts else "No examples available."

        return formatted_schema, formatted_examples, retrieved_tables


def get_rag_index(persist_directory: Optional[str] = None) -> SchemaRAGIndex:
    """Factory helper to obtain an initialized SchemaRAGIndex instance."""
    return SchemaRAGIndex(persist_directory=persist_directory)

