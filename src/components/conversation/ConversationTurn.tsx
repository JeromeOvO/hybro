import type { ConversationTurnView, ConversationBlock } from '@/lib/selectors/conversation-types'
import { UserMessageBlock } from './UserMessageBlock'
import { AgentCard } from './AgentCard'
import { AgentContentBlock } from './AgentContentBlock'
import { UserAnswerCard } from './UserAnswerCard'
import { UnresolvedAgentGroup } from './UnresolvedAgentGroup'

interface ConversationTurnProps {
  turn: ConversationTurnView
  onUserSentinelRef?: (el: HTMLDivElement | null) => void
  multiAgentTurn: boolean
}

function BlockRenderer({ block, multiAgent }: { block: ConversationBlock; multiAgent: boolean }) {
  switch (block.type) {
    case 'agent_card':
      return <div style={{ padding: '0 var(--conversation-padding-inner)' }}><AgentCard {...block} /></div>
    case 'agent_content':
      return <AgentContentBlock {...block} showAttribution={multiAgent} />
    case 'user_answer':
      return <div style={{ padding: '0 var(--conversation-padding-inner)' }}><UserAnswerCard {...block} /></div>
    case 'agent_divider':
      return (
        <div style={{ padding: '0 var(--conversation-padding-inner)', margin: '12px 0' }}>
          <div style={{ height: 1, backgroundColor: 'var(--conversation-border-subtle)' }} />
        </div>
      )
    case 'unresolved_content':
      return (
        <div className="text-sm" style={{ padding: '0 var(--conversation-padding-inner)', color: 'var(--conversation-text-tertiary)' }}>
          {block.entity.content}
        </div>
      )
    default:
      return null
  }
}

export function ConversationTurn({ turn, onUserSentinelRef, multiAgentTurn }: ConversationTurnProps) {
  if (turn.userMessage === null) {
    return <UnresolvedAgentGroup blocks={turn.blocks} />
  }

  return (
    <div className="flex flex-col" style={{ gap: 'var(--conversation-gap-block)' }}>
      <UserMessageBlock entity={turn.userMessage} onSentinelRef={onUserSentinelRef} />
      {turn.blocks.map((block, i) => (
        <BlockRenderer key={i} block={block} multiAgent={multiAgentTurn} />
      ))}
    </div>
  )
}
