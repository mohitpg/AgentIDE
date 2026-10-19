"""
Bake the RAG models into an image at build time.

Run by infra/docker/python.Dockerfile once the third-party deps are installed.
Images whose package pulls in agentide-rag (api, workers, agent-rag) get the
fastembed embedding model, the cross-encoder reranker and tiktoken's BPE file,
so a new pod doesn't download them on its first request. Other images don't
have these libraries, and the script does nothing.

Files land in HF_HOME, FASTEMBED_CACHE_PATH and TIKTOKEN_CACHE_DIR, which the
Dockerfile sets to the same paths in the build and runtime stages.
"""

from __future__ import annotations

import importlib.util

# The RagConfig defaults (packages/rag/src/rag/config.py). That package isn't
# installed yet when this runs, so keep these in step with it.
EMBEDDING_MODEL = "BAAI/bge-small-en-v1.5"
RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"
TIKTOKEN_ENCODING = "cl100k_base"


def main() -> None:
    if importlib.util.find_spec("sentence_transformers") is None:
        print("preload_models: no agentide-rag in this image; nothing to download")
        return

    import tiktoken
    from fastembed import TextEmbedding
    from sentence_transformers import CrossEncoder

    # The same constructors as rag.embeddings / rag.reranker / rag.chunking, plus
    # one inference each, so a broken download fails the build instead of a pod.
    list(TextEmbedding(model_name=EMBEDDING_MODEL).embed(["warm up"]))
    CrossEncoder(RERANKER_MODEL).predict([("warm", "up")])
    tiktoken.get_encoding(TIKTOKEN_ENCODING).encode("warm up")
    print(f"preload_models: cached {EMBEDDING_MODEL}, {RERANKER_MODEL}, {TIKTOKEN_ENCODING}")


if __name__ == "__main__":
    main()
