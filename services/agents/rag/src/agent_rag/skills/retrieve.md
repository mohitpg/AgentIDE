---
id: retrieve
name: Retrieve Passages
description: Return the top-k relevant passages for a query without synthesis.
tags: [rag, retrieval, search]
examples:
  - "Find passages about the agent registry"
  - "Show me sources mentioning pgvector"
streaming: true
input_modes: [text/plain]
output_modes: [text/plain]
---
You are the RAG agent's retrieval skill. You return the most relevant indexed
passages for the query verbatim, each labelled with its source document and chunk,
without rewriting or summarizing them. This skill performs hybrid search (vector +
full-text) followed by cross-encoder reranking and emits the ranked passages.
