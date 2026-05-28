/** Provenance for a quote (matches backend ``QuoteSourceKind``). */
export type QuoteSourceKind = 'agent' | 'synthesis' | 'user_turn' | 'unknown'

/** Max quote length enforced client-side (see QUOTE_REPLY §8.10). */
export const MAX_QUOTE_TEXT_LENGTH = 8000

/** Lightweight UI type for passing quote data between components. */
export interface QuoteData {
  messageId: string
  content: string
  senderName: string
  /** DOM ``data-quote-source-kind``; defaults to ``unknown`` if omitted. */
  sourceKind?: QuoteSourceKind
  sourceAgentId?: string
}

/** Wire shape for ``message.quote`` on send (backend ``UserQuoteCreatePayload``). */
export interface RoomQuoteWire {
  text: string
  source_message_id: string
  source_kind: QuoteSourceKind
  sender_display_name?: string | null
  source_agent_id?: string | null
}
