import type { MessageEntity, ArtifactData } from '@/stores/message-store/types'
import type { HITLPromptType } from '@/lib/types/sse'
import { AGENT_PALETTE, agentColorIndex } from '@/lib/mention-color'

// ── Agent theme ─────────────────────────────────────────────

export interface AgentTheme {
  name: string
  accent: string
  border: string
  avatarBg: string
  avatarLightBg: string
  cardBg: string
}

export const AGENT_THEMES: AgentTheme[] = AGENT_PALETTE.map(({ name }, i) => ({
  name,
  accent: `hsl(var(--agent-color-${i}))`,
  border: `hsl(var(--agent-color-${i}) / 0.18)`,
  avatarBg: `hsl(var(--agent-color-${i}) / 0.06)`,
  avatarLightBg: `hsl(var(--agent-color-${i}) / 0.12)`,
  cardBg: `hsl(var(--agent-color-${i}) / 0.08)`,
}))

export const UNRESOLVED_THEME: AgentTheme = {
  name: 'muted', accent: 'var(--conversation-text-muted)', border: 'var(--conversation-border)', avatarBg: '#27272a', avatarLightBg: 'rgba(113, 113, 122, 0.15)', cardBg: 'hsl(var(--color-card))',
}

export function getAgentTheme(agentId: string | undefined, agentName: string): AgentTheme {
  const key = agentId ?? agentName
  return AGENT_THEMES[agentColorIndex(key)]
}

// ── Agent display props ─────────────────────────────────────

export interface AgentDisplayProps {
  label: string
  tone: 'accent' | 'muted' | 'danger' | 'warning'
  isAnimated: boolean
  ariaLabel: string
}

// ── Conversation blocks ─────────────────────────────────────

export type ConversationBlock =
  | { type: 'agent_card'; messageId: string; agentId: string; agentName: string; display: AgentDisplayProps; taskDescription: string; theme: AgentTheme; agentSource?: 'cloud' | 'hub'; isStreaming?: boolean }
  | { type: 'agent_content'; agentId: string; agentName: string; content: string; isStreaming: boolean; artifacts?: ArtifactData[] }
  | { type: 'user_answer'; agentName: string; question: string; answer: string }
  | { type: 'agent_divider' }
  | { type: 'unresolved_content'; entity: MessageEntity }

// ── Conversation turn view ──────────────────────────────────

export interface ConversationTurnView {
  turnId: string
  userMessage: MessageEntity | null
  blocks: ConversationBlock[]
}

// ── HITL ────────────────────────────────────────────────────

export interface PendingHitl {
  hitlId: string
  agentName: string
  question: string
  promptType: HITLPromptType
  choices?: string[]
  messageId: string
  groupId?: string
  groupTotal?: number
  groupIndex?: number
  isAnswered: boolean
}

export interface HitlState {
  hitlId: string
  resolved: boolean
  question: string
  answer: string | null
}

// ── Composer ────────────────────────────────────────────────

export interface ComposerState {
  mode: 'normal' | 'hitl_responding'
  isProcessing: boolean
  pendingHitls: PendingHitl[]
}

// ── Content view ────────────────────────────────────────────

export interface ContentView {
  text: string
  isStreaming: boolean
}

export interface AgentResponseDetail {
  messageId: string
  agentId: string
  agentName: string
  display: AgentDisplayProps
  taskDescription: string
  theme: AgentTheme
  content: string
  isStreaming: boolean
  artifacts?: ArtifactData[]
  taskStatus?: MessageEntity['taskStatus']
  taskStatusMessage?: string | null
  taskError?: string | null
  requestMessage: MessageEntity | null
  agentSource?: 'cloud' | 'hub'
}
