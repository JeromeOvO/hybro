import type { AgentDisplayProps } from '@/lib/selectors/conversation-types'
import type { AgentResultViewModel } from './types'

function make(
  name: string,
  label: string,
  tone: AgentDisplayProps['tone'],
  isAnimated: boolean,
): AgentDisplayProps {
  return { label, tone, isAnimated, ariaLabel: `${name} — ${label}` }
}

export function mapResultDisplayProps(
  result: AgentResultViewModel,
  isStreaming: boolean,
  displayContent?: string,
): AgentDisplayProps {
  const name = result.agentName
  const visibleContent = (displayContent ?? result.content).trim()

  switch (result.status) {
    case 'working':
      if (result.isSummaryAgent) {
        return make(name, 'Synthesizing', 'accent', true)
      }
      return isStreaming && visibleContent.length > 0
        ? make(name, 'Streaming', 'accent', true)
        : make(name, 'Working', 'accent', true)
    case 'completed':
      return make(name, 'Completed', 'muted', false)
    case 'failed':
      return make(name, 'Failed', 'danger', false)
    case 'awaiting_input':
      return make(name, 'Needs Input', 'warning', true)
  }
}
