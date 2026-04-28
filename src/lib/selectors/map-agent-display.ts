import type { MessageEntity } from '@/stores/message-store/types'
import type { AgentDisplayProps } from './conversation-types'
import type { TaskState } from '@/lib/types/sse'

function relativeTime(isoDate: string | undefined | null): string {
  if (!isoDate) return ''
  const diffMs = Date.now() - new Date(isoDate).getTime()
  const mins = Math.floor(diffMs / 60_000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  return `${Math.floor(hours / 24)}d ago`
}

function make(name: string, label: string, tone: AgentDisplayProps['tone'], isAnimated: boolean): AgentDisplayProps {
  return { label, tone, isAnimated, ariaLabel: `${name} — ${label}` }
}

export function mapAgentDisplayProps(entity: MessageEntity): AgentDisplayProps {
  const name = entity.senderName ?? 'Agent'
  const status: TaskState | null | undefined = entity.taskStatus

  if (status == null) return make(name, 'Working', 'accent', true)

  switch (status) {
    case 'submitted':
      return make(name, 'Working', 'accent', true)

    case 'working': {
      const hasContent = (entity.content ?? '').trim().length > 0
      return hasContent
        ? make(name, 'Streaming', 'accent', true)
        : make(name, 'Working', 'accent', true)
    }

    case 'completed': {
      const time = relativeTime(entity.taskUpdatedAt)
      const label = time ? `Completed · ${time}` : 'Completed'
      return make(name, label, 'muted', false)
    }

    case 'failed':
      return make(name, 'Failed', 'danger', false)

    case 'rejected':
      return make(name, 'Rejected', 'danger', false)

    case 'canceled':
      return make(name, 'Canceled', 'muted', false)

    case 'input-required':
      return make(name, 'Needs Input', 'warning', true)

    case 'auth-required':
      return make(name, 'Auth Required', 'warning', false)

    case 'unknown':
      return make(name, 'Working', 'accent', true)

    default: {
      const _exhaustive: never = status
      return _exhaustive
    }
  }
}
