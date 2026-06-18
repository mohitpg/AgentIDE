// Parse the RAG agent's answer into its prose body and the `Sources:` footer.
//
// The agent appends a deterministic footer: "\n\nSources:\n[1] file (chunk 0)\n…"
// (see services/agents/rag). We split that off so the chat can render the body
// with clickable inline [n] markers and a row of source chips.

export interface Source {
  n: number
  filename: string
  chunk: number
}

export interface ParsedAnswer {
  body: string
  sources: Source[]
}

const SOURCES_MARKER = '\n\nSources:\n'
const SOURCE_LINE = /^\[(\d+)\]\s+(.*?)\s+\(chunk\s+(\d+)\)\s*$/

export function parseAnswer(content: string): ParsedAnswer {
  const idx = content.lastIndexOf(SOURCES_MARKER)
  if (idx === -1) return { body: content, sources: [] }

  const footer = content.slice(idx + SOURCES_MARKER.length)
  const sources: Source[] = []
  for (const line of footer.split('\n')) {
    const m = SOURCE_LINE.exec(line.trim())
    if (m) sources.push({ n: Number(m[1]), filename: m[2], chunk: Number(m[3]) })
  }

  // If nothing parsed, keep the original text intact rather than dropping the footer.
  if (sources.length === 0) return { body: content, sources: [] }
  return { body: content.slice(0, idx), sources }
}
