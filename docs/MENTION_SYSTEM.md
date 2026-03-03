# Mention System — @Agent Autocomplete

> **Status: Implemented**

---

## 1. Overview

Users can type `@` in the chat input to trigger an autocomplete dropdown listing available
agents. Selecting an agent inserts a styled mention chip into the contenteditable editor.
When a message contains mentions, they override the group selector — the message is
targeted only to the mentioned agents.

---

## 2. User Flow

1. User types `@` in the contenteditable editor.
2. An autocomplete dropdown appears below the cursor, listing agents.
3. Typing after `@` filters the list (e.g., `@Res` shows only agents matching "Res").
4. User selects an agent via click, Enter, or Tab.
5. A styled mention chip (`@AgentName`) is inserted, replacing the `@query` text.
6. The group selector shows a mention indicator (e.g., "@AgentName" or "N agents mentioned") and disables the dropdown.
7. On send, `targetGroup` is `undefined` (mentions take priority over groups).
8. Multiple agents can be mentioned in a single message.

---

## 3. Architecture

### Storage Format

Mentions use a custom inline format in the message string:

```
<@agentId|agentName>
```

Example: `Hey <@abc123|ResearchBot>, can you look into this?`

This format is the canonical representation stored in the `message` state and sent to the
backend. The contenteditable editor displays a rendered HTML version with styled spans.

### Display Rendering

`convertToDisplayHTML(content)` transforms storage format into editor-safe HTML:

```html
<span class="room-mention" data-id="abc123" data-name="ResearchBot" contenteditable="false">
  @ResearchBot
</span>
```

The `contenteditable="false"` attribute makes mention chips behave as atomic inline elements
that cannot be partially edited — they can only be deleted as a whole unit.

### Storage Extraction

`convertToStorageFormat()` traverses the contenteditable DOM tree and converts
`.room-mention` spans back to `<@id|name>` format by reading `data-id` and `data-name`
attributes.

### Autocomplete Trigger

The `handleInput()` function checks text before the cursor position with regex `/@(\w*)$/`.
If matched, `showAgentSuggestions` is set to `true` and `mentionQuery` is updated for
filtering.

### Agent Insertion

`insertMention(agent)` is the most complex function in the mention system:

1. Gets current text in both storage format and display format.
2. Finds the `@` trigger position in display text.
3. Maps that position to the corresponding offset in storage format (accounting for existing `<@id|name>` tokens which are longer than their display representation).
4. Splices `<@${agent.id}|${agent.name}> ` into the storage string.
5. Updates the editor HTML via `convertToDisplayHTML()`.
6. Restores cursor position after the inserted mention.

### Mention-Group Interaction

When `mentionedAgents.length > 0`, the group selector is replaced with a mention indicator
pill. The `targetGroup` passed to `onSubmit` is `undefined`, signaling to the backend that
targeting should be derived from the mentioned agents rather than a group.

`mentionedAgents` is extracted via `useMemo` with regex `/<@([^|]+)\|([^>]+)>/g` on the
current message string.

### Keyboard Navigation

When the autocomplete dropdown is visible:
- **ArrowDown/ArrowUp** — cycle through suggestions.
- **Enter/Tab** — confirm the highlighted suggestion.
- **Escape** — dismiss the dropdown.

---

## 4. Code References

| Concept | File | Notes |
|---|---|---|
| Storage format & state | `src/components/room-chat-input.tsx` (line 103) | `message` state holds `<@id\|name>` format |
| Autocomplete trigger | `src/components/room-chat-input.tsx` (lines 356-366) | Regex `/@(\w*)$/` on text before cursor |
| Agent filtering | `src/components/room-chat-input.tsx` (lines 113-115) | Case-insensitive name filter |
| `insertMention()` | `src/components/room-chat-input.tsx` (lines 425-534) | Storage-to-display position mapping and DOM update |
| `convertToDisplayHTML()` | `src/components/room-chat-input.tsx` (lines 141-177) | Storage format to styled HTML spans |
| `convertToStorageFormat()` | `src/components/room-chat-input.tsx` (lines 223-246) | DOM traversal back to storage format |
| `mentionedAgents` extraction | `src/components/room-chat-input.tsx` (lines 597-605) | `useMemo` with regex extraction |
| Mention override logic | `src/components/room-chat-input.tsx` (lines 614-617) | `targetGroup = undefined` when mentions exist |
| Keyboard navigation | `src/components/room-chat-input.tsx` (lines 550-594) | ArrowDown/Up, Enter/Tab, Escape |
| Group selector indicator | `src/components/group-selector.tsx` | Shows `@agentName` pill when `mentionedAgents` is non-empty |
| Mention rendering (agent messages) | `src/components/markdown-content.tsx` (lines 73-78) | `processMentions()` converts `<@id\|name>` to clickable markdown links `[@name](/c/agents/id)` |
| Mention rendering (user messages) | `src/components/markdown-content.tsx` (lines 224-247) | `LinkifiedContent` parses mentions into styled `<a>` tags |

---

## 5. Known Limitations

- **Word-boundary only**: The trigger regex `/@(\w*)$/` only matches word characters after `@`. Agent names with spaces, hyphens, or special characters may not autocomplete correctly.
- **No mention in quoted replies**: If quoted text contains a mention, it is sent as the raw storage format.
- **Full agent list loaded**: The autocomplete filters from the full agent list passed as props. No lazy loading or server-side search for large agent catalogs.
- **ContentEditable complexity**: The DOM-based approach for mention insertion is complex (~110 lines in `insertMention`) and fragile across browsers. Future improvement could adopt a library like Slate or TipTap.
- **Rendering differs by context**: Agent messages render mentions as clickable markdown links via `processMentions()` in `markdown-content.tsx`. User messages render them as styled `<a>` tags via `LinkifiedContent`. The styling is not identical between the two paths.
