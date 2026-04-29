import type { ConversationTurnView, ConversationBlock } from '@/lib/selectors/conversation-types'
import { UserMessageBlock } from './UserMessageBlock'
import { AgentCard } from './AgentCard'
import { AgentContentBlock } from './AgentContentBlock'
import { UserAnswerCard } from './UserAnswerCard'
import { UnresolvedAgentGroup } from './UnresolvedAgentGroup'

interface ConversationTurnProps {
  turn: ConversationTurnView
  multiAgentTurn: boolean
}

function BlockRenderer({ block, multiAgent }: { block: ConversationBlock; multiAgent: boolean }) {
  switch (block.type) {
    case 'agent_card':
      return <AgentCard {...block} />
    case 'agent_content':
      return <AgentContentBlock {...block} showAttribution={multiAgent} />
    case 'user_answer':
      return <UserAnswerCard {...block} />
    case 'agent_divider':
      return (
        <div style={{ margin: '12px 0' }}>
          <div style={{ height: 1, backgroundColor: 'var(--conversation-border-subtle)' }} />
        </div>
      )
    case 'unresolved_content':
      return (
        <div className="text-sm" style={{ color: 'var(--conversation-text-tertiary)' }}>
          {block.entity.content}
        </div>
      )
    default:
      return null
  }
}

export function ConversationTurn({ turn, multiAgentTurn }: ConversationTurnProps) {
  if (turn.userMessage === null) {
    return (
      <div className="conversation-turn">
        <UnresolvedAgentGroup blocks={turn.blocks} />
      </div>
    )
  }

  return (
    <div className="conversation-turn">
      <div className="conversation-user-sticky">
        <UserMessageBlock entity={turn.userMessage} />
      </div>

      {turn.blocks.length > 0 && (
        <div
          className="conversation-body-frame flex flex-col"
          style={{ gap: 'var(--conversation-gap-block)', paddingTop: 10 }}
        >
          {turn.blocks.map((block, i) => (
            <BlockRenderer key={i} block={block} multiAgent={multiAgentTurn} />
          ))}
        </div>
      )}
    </div>
  )
}
