// Inline image handling for the composer and message bubbles.
//
// Images no longer ride to the backend as base64 — on Send the composer uploads
// each attachment to POST /images and embeds the returned `<Image at db.images.
// {id}>` reference in the message text. The Vision agent resolves that reference
// (GET /images/{id}); the message bubble renders the same endpoint as a thumbnail.
// These helpers keep the wire format (text + appended references) and the chip
// preview (a local data URI, never sent) in sync on the client.

// A stored-image reference token. Mirrors the gateway + agent regex.
const IMAGE_REF_RE = /<Image at db\.images\.([0-9a-fA-F-]{36})>/g

// Where an attachment is in its POST /images lifecycle. Upload now happens the
// moment a file is attached (not on Send), so the chip can confirm success.
export type AttachmentStatus = 'uploading' | 'done' | 'error'

export interface Attachment {
  // Stable client-side id, used as the React key and to update this chip in place
  // when its async upload resolves.
  localId: string
  // The original file, uploaded to POST /images on attach.
  file: File
  // A local `data:image/...` URI used only for the composer chip preview.
  dataUri: string
  // Original filename (or a synthesized name for pasted images), for the chip.
  name: string
  // Upload progress: 'uploading' → 'done' (ref set) or 'error' (error set).
  status: AttachmentStatus
  // The `<Image at db.images.{id}>` token, set once the upload succeeds.
  ref?: string
  // Failure detail, set when status is 'error'.
  error?: string
}

// Read a File/Blob as a base64 data URI (for the local chip preview).
export function fileToDataUri(file: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result))
    reader.onerror = () => reject(reader.error ?? new Error('Failed to read image'))
    reader.readAsDataURL(file)
  })
}

// Build the wire content: typed text followed by each uploaded image's reference.
// The agent's extractor tolerates any position, but trailing keeps the prompt clean.
export function withImageRefs(text: string, refs: string[]): string {
  if (refs.length === 0) return text
  return [text.trim(), ...refs].filter(Boolean).join('\n')
}

// Split a message's content back into its stored-image ids and the residual
// display text, so a sent/persisted message renders thumbnails (via GET
// /images/{id}) instead of the raw reference tokens.
export function splitImageRefs(content: string): { ids: string[]; text: string } {
  const ids = [...content.matchAll(IMAGE_REF_RE)].map((m) => m[1])
  const text = content.replace(IMAGE_REF_RE, '').replace(/\n{3,}/g, '\n\n').trim()
  return { ids, text }
}
