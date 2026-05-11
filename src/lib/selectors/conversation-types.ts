import type { MessageEntity, ArtifactData } from '@/stores/message-store/types'
import type { HITLPromptType } from '@/lib/types/sse'

// ── Agent theme ─────────────────────────────────────────────

export interface AgentTheme {
  name: string
  accent: string
  border: string
  avatarBg: string
  avatarLightBg: string
  cardBg: string
}

export const AGENT_THEMES: AgentTheme[] = [
  { name: 'green',  accent: 'var(--conversation-agent-green)',  border: 'var(--conversation-agent-border-green)',  avatarBg: 'var(--conversation-agent-bg-green)',  avatarLightBg: 'var(--conversation-agent-avatar-green)',  cardBg: 'var(--conversation-agent-card-green)' },
  { name: 'blue',   accent: 'var(--conversation-agent-blue)',   border: 'var(--conversation-agent-border-blue)',   avatarBg: 'var(--conversation-agent-bg-blue)',   avatarLightBg: 'var(--conversation-agent-avatar-blue)',   cardBg: 'var(--conversation-agent-card-blue)' },
  { name: 'purple', accent: 'var(--conversation-agent-purple)', border: 'var(--conversation-agent-border-purple)', avatarBg: 'var(--conversation-agent-bg-purple)', avatarLightBg: 'var(--conversation-agent-avatar-purple)', cardBg: 'var(--conversation-agent-card-purple)' },
  { name: 'amber',  accent: 'var(--conversation-agent-amber)',  border: 'var(--conversation-agent-border-amber)',  avatarBg: 'var(--conversation-agent-bg-amber)',  avatarLightBg: 'var(--conversation-agent-avatar-amber)',  cardBg: 'var(--conversation-agent-card-amber)' },
  { name: 'rose',   accent: 'var(--conversation-agent-rose)',   border: 'var(--conversation-agent-border-rose)',   avatarBg: 'var(--conversation-agent-bg-rose)',   avatarLightBg: 'var(--conversation-agent-avatar-rose)',   cardBg: 'var(--conversation-agent-card-rose)' },
]

export const UNRESOLVED_THEME: AgentTheme = {
  name: 'muted', accent: 'var(--conversation-text-muted)', border: 'var(--conversation-border)', avatarBg: '#27272a', avatarLightBg: 'rgba(113, 113, 122, 0.15)', cardBg: 'hsl(var(--color-card))',
}

export function getAgentTheme(agentId: string | undefined, agentName: string): AgentTheme {
  const key = agentId ?? agentName
  let hash = 0
  for (let i = 0; i < key.length; i++) {
    hash = ((hash << 5) - hash + key.charCodeAt(i)) | 0
  }
  return AGENT_THEMES[Math.abs(hash) % AGENT_THEMES.length]
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
