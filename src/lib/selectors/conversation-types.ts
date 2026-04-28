import type { MessageEntity, ArtifactData } from '@/stores/message-store/types'
import type { HITLPromptType } from '@/lib/types/sse'

// ── Agent theme ─────────────────────────────────────────────

export interface AgentTheme {
  name: string
  accent: string   // CSS custom property name, e.g. '--conversation-agent-green'
  bg: string       // subtle background tint class
}

export const AGENT_THEMES: AgentTheme[] = [
  { name: 'green',  accent: 'var(--conversation-agent-green)',  bg: 'bg-green-500/5' },
  { name: 'blue',   accent: 'var(--conversation-agent-blue)',   bg: 'bg-blue-500/5' },
  { name: 'purple', accent: 'var(--conversation-agent-purple)', bg: 'bg-purple-500/5' },
  { name: 'amber',  accent: 'var(--conversation-agent-amber)',  bg: 'bg-amber-500/5' },
  { name: 'rose',   accent: 'var(--conversation-agent-rose)',   bg: 'bg-rose-500/5' },
]

export const UNRESOLVED_THEME: AgentTheme = {
  name: 'muted', accent: 'var(--conversation-text-muted)', bg: 'bg-zinc-500/5',
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
  | { type: 'agent_card'; agentId: string; agentName: string; display: AgentDisplayProps; taskDescription: string; theme: AgentTheme }
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
