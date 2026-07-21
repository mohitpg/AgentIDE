"""create rag tables (pgvector + full-text)

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-16 00:00:00.000000

RAG storage with two tables:

  rag_documents — one row per ingested document (metadata + chunk count).
  rag_chunks    — one row per chunk: content, a 384-dim `embedding` (HNSW,
                  cosine) for semantic search, and a generated `tsvector`
                  (GIN) for full-text / BM25-style keyword search.

These tables are intentionally NOT declared as ORM models on Base.metadata —
`packages/rag` owns all access via parameterised SQL, keeping it decoupled from
the API. They are therefore managed by hand-written migrations like this one,
not by Alembic autogenerate.

The vector dimension (384) is tied to the embedding model (BAAI/bge-small-en
v1.5). Changing the model means a new migration that recreates the column/index.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.execute(
        """
        CREATE TABLE rag_documents (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            filename     text NOT NULL,
            content_type text,
            source       text NOT NULL DEFAULT 'upload',
            session_id   uuid REFERENCES conversations(id) ON DELETE SET NULL,
            num_chunks   integer NOT NULL DEFAULT 0,
            byte_size    integer,
            meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at   timestamptz NOT NULL DEFAULT now(),
            updated_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE rag_chunks (
            id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            document_id  uuid NOT NULL REFERENCES rag_documents(id) ON DELETE CASCADE,
            chunk_index  integer NOT NULL,
            content      text NOT NULL,
            token_count  integer,
            embedding    vector(384) NOT NULL,
            tsv          tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            meta         jsonb NOT NULL DEFAULT '{}'::jsonb,
            created_at   timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute("CREATE INDEX ix_rag_documents_session_id ON rag_documents (session_id)")
    op.execute("CREATE INDEX ix_rag_chunks_document_id ON rag_chunks (document_id)")
    # HNSW approximate-nearest-neighbour index for cosine-distance vector search.
    op.execute(
        "CREATE INDEX ix_rag_chunks_embedding_hnsw "
        "ON rag_chunks USING hnsw (embedding vector_cosine_ops)"
    )
    # GIN index over the generated tsvector for full-text / BM25-style search.
    op.execute("CREATE INDEX ix_rag_chunks_tsv ON rag_chunks USING gin (tsv)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS rag_chunks")
    op.execute("DROP TABLE IF EXISTS rag_documents")
    # Leave the `vector` extension in place; other tables may rely on it.
