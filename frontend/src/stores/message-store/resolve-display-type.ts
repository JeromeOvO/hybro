import type { DisplayType } from './types'

/**
 * Resolve the display type for a message. Agent messages always render as
 * 'agent-bubble' — the unified AgentMessageBubbleInner handles all phases
 * (waiting, streaming, interactive, failed, complete, complete-empty)
 * via `derivePhase` at render time.
 */
export function resolveDisplayType(msg: {
  messageType: 'user' | 'agent'
}): DisplayType {
  return msg.messageType === 'user' ? 'user-bubble' : 'agent-bubble'
}
