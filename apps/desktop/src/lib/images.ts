// Inline image handling for the composer and message bubbles.
//
// Images ride to the backend embedded in the message text as base64 data URIs —
// the chat transport carries a single `content` string, and the vision agent
// pulls the data URIs back out of it (see services/agents/vision images.py).
// These helpers keep the wire format (text + appended data URIs) and the display
// format (residual text + thumbnails) in sync on the client.

// A base64 image data URI, up to the first whitespace. Mirrors the agent regex.
const DATA_URI_RE = /data:image\/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=]+/g

export interface Attachment {
  // The full `data:image/...;base64,...` URI sent to the backend.
  dataUri: string
  // Original filename (or a synthesized name for pasted images), for the chip.
  name: string
}

// Read a File/Blob as a base64 data URI.
export function fileToDataUri(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read image'))
    reader.readAsDataURL(file)
  })
}

// Build the wire content: typed text followed by each image's data URI. The
// agent's extractor tolerates any position, but trailing keeps the prompt clean.
export function withImages(text: string, attachments: Attachment[]): string {
  if (attachments.length === 0) return text
  return [text.trim(), ...attachments.map((a) => a.dataUri)].filter(Boolean).join('\n')
}

// Split a message's content back into its images and the residual display text,
// so a sent/persisted message renders thumbnails instead of a raw base64 blob.
export function splitImages(content: string): { images: string[]; text: string } {
  const images = content.match(DATA_URI_RE) ?? []
  const text = content.replace(DATA_URI_RE, '').replace(/\n{3,}/g, '\n\n').trim()
  return { images, text }
}
