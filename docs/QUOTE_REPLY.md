# Quote Reply — Text Selection Quote System

> **Status: Implemented**

---

## 1. Overview

Users can select text within an agent's message bubble, click a floating "Quote" button,
and reply with that text as context. The quoted text appears in a preview bar above the
chat input and is sent to the backend as `quoted_text` in the message payload.

This enables contextual follow-up questions without copy-pasting, particularly useful in
multi-agent rooms where users need to reference a specific agent's response.

---

## 2. User Flow

1. User selects text inside an agent message bubble.
2. A floating "Quote" button appears above the selection (positioned via `getBoundingClientRect()`).
3. User clicks the button.
4. The selected text and agent name populate a quote preview bar in `RoomChatInput`.
5. The text selection is cleared and the input auto-focuses.
6. User types their follow-up message and sends.
7. `quoted_text` is included in the `SendMessage` API payload.
8. The quote preview is cleared after send.

### Dismiss Behavior

- Clicking outside the message bubble and quote button dismisses the button.
- A global `mousedown` listener handles this (with `e.preventDefault()` on the button itself to prevent clearing the selection before the click registers).

---

## 3. Architecture

### QuoteData Type

```typescript
interface QuoteData {
  messageId: string
  content: string    // the selected text
  senderName: string // agent display name
}
```

### Quote Button (Native DOM)

The quote button is implemented entirely with native DOM operations (no React rendering)
for performance — `document.createElement('button')` appended to `document.body` with
`position: fixed` and `z-index: 9999`. This avoids re-rendering the entire message bubble
on text selection changes.

Key functions in `message-bubble.tsx`:
- `showQuoteButton(top, left, text)` — creates and positions the DOM button.
- `hideQuoteButton()` — removes the DOM button.
- `handleMouseUp()` — fires on `onMouseUp`, uses `requestAnimationFrame` to let the browser finalize selection, validates the selection is within the content ref, and positions the button.

### Quote Preview Bar

Rendered in `room-chat-input.tsx` when `quote` prop is set. Displays:
- A vertical primary-colored indicator bar.
- A Quote icon and the sender name.
- The quoted text (clamped to 2 lines).
- An `X` button to dismiss the quote.

### API Integration

The `onSubmit` handler in `room-chat-input.tsx` passes the `QuoteData` to the parent.
The `sendUserMessage` function in `useRoomWebhook` includes `quoted_text` in the
`SendMessage` API call payload.

---

## 4. Code References

| Concept | File | Notes |
|---|---|---|
| QuoteData type | `src/components/message-bubble.tsx` (lines 14-18) | Exported interface |
| Quote button logic | `src/components/message-bubble.tsx` (lines 187-291) | `showQuoteButton`, `hideQuoteButton`, `handleMouseUp`, global dismiss listener |
| onQuote callback | `src/components/message-bubble.tsx` | `AgentMessageBubbleInner` accepts `onQuote?: (data: QuoteData) => void` |
| Quote preview bar | `src/components/room-chat-input.tsx` (lines 738-762) | Renders above the editor |
| Auto-focus on quote | `src/components/room-chat-input.tsx` (lines 537-541) | Editor focuses when quote is set |
| API payload | `src/lib/api/room.ts` | `quoted_text` nested in `message.extend_info` (line ~218) |

---

## 5. Known Limitations

- **Text-only quotes**: Only plain text can be quoted. Code blocks, images, and structured content are quoted as their text representation.
- **No visual quote in message history**: Sent messages do not display the quoted context inline. The `quoted_text` is sent to the backend but there is no rendering of received quotes in message bubbles.
- **Single quote only**: Only one quote can be active at a time. Selecting new text replaces the previous quote.
- **No keyboard shortcut**: Quote is only accessible via mouse text selection; no keyboard-triggered quote flow exists.
