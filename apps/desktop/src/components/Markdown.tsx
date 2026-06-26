import { Children, createElement, type ComponentType, type ReactNode } from 'react'
import AIMarkdown, { type AIMarkdownCustomComponents } from '@ai-react-markdown/core'

import type { Source } from '../lib/citations'

interface Props {
  content: string
  streaming?: boolean
  sources?: Source[]
  onOpenSource?: (filename: string) => void
}

// Split a text run on inline [n] markers, turning each one whose number is in the
// footer into a clickable citation badge (same behaviour as the old renderBody).
function citationize(
  text: string,
  sources: Source[],
  onOpenSource?: (filename: string) => void,
): ReactNode[] {
  return text.split(/(\[\d+\])/g).map((part, i) => {
    const m = /^\[(\d+)\]$/.exec(part)
    const src = m ? sources.find((s) => s.n === Number(m[1])) : undefined
    if (m && src) {
      return (
        <button
          key={i}
          className="citation-ref"
          title={`${src.filename} · chunk ${src.chunk}`}
          onClick={() => onOpenSource?.(src.filename)}
        >
          {src.n}
        </button>
      )
    }
    return part
  })
}

// Walk a node's children and citationize only the string leaves, leaving nested
// markdown elements (bold, code, links, …) untouched.
function inject(
  children: ReactNode,
  sources: Source[],
  onOpenSource?: (filename: string) => void,
): ReactNode {
  return Children.map(children, (child) =>
    typeof child === 'string' ? citationize(child, sources, onOpenSource) : child,
  )
}

// Build react-markdown component overrides for the inline-text-bearing elements
// where [n] markers appear, so citations survive markdown rendering. Only needed
// when the answer carries a parsed Sources: footer.
function citationComponents(
  sources: Source[],
  onOpenSource?: (filename: string) => void,
): AIMarkdownCustomComponents {
  const wrap = (tag: string): ComponentType<{ children?: ReactNode; node?: unknown }> =>
    function Wrapped({ children, node: _node, ...props }) {
      return createElement(tag, props, inject(children, sources, onOpenSource))
    }
  const tags = ['p', 'li', 'td', 'th', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6']
  return Object.fromEntries(tags.map((t) => [t, wrap(t)])) as AIMarkdownCustomComponents
}

// Renders LLM output as GitHub-flavored markdown (streaming-aware), re-injecting
// the [n] citation badges that the RAG footer references.
export default function Markdown({ content, streaming, sources = [], onOpenSource }: Props): JSX.Element {
  return (
    <AIMarkdown
      content={content}
      streaming={streaming}
      colorScheme="dark"
      customComponents={sources.length > 0 ? citationComponents(sources, onOpenSource) : undefined}
    />
  )
}
