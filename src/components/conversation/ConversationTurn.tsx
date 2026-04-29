import { useRef, useEffect, useState } from 'react'
import type { ConversationTurnView, ConversationBlock } from '@/lib/selectors/conversation-types'
import { UserMessageBlock } from './UserMessageBlock'
import { AgentCard } from './AgentCard'
import { AgentContentBlock } from './AgentContentBlock'
import { UserAnswerCard } from './UserAnswerCard'
import { UnresolvedAgentGroup } from './UnresolvedAgentGroup'

interface ConversationTurnProps {
  turn: ConversationTurnView
  isStuck?: boolean
  onSentinelRef?: (el: HTMLDivElement | null) => void
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

export function ConversationTurn({ turn, isStuck, onSentinelRef, multiAgentTurn }: ConversationTurnProps) {
  const prevStuckRef = useRef(isStuck)
  const [fadeKey, setFadeKey] = useState(0)

  useEffect(() => {
    if (isStuck && !prevStuckRef.current) {
      setFadeKey(k => k + 1)
    }
    prevStuckRef.current = isStuck
  }, [isStuck])

  if (turn.userMessage === null) {
    return <UnresolvedAgentGroup blocks={turn.blocks} />
  }

  return (
    <div>
      <div ref={onSentinelRef} style={{ height: 0 }} />
      <div
        key={fadeKey}
        className={isStuck ? 'conversation-sticky-fade-in' : undefined}
        style={{
          position: 'sticky',
          top: 0,
          zIndex: isStuck ? 15 : 10,
          paddingTop: isStuck ? 'var(--conversation-sticky-top)' : undefined,
          paddingBottom: 4,
          background: isStuck ? 'hsl(var(--background))' : undefined,
        }}
      >
        <UserMessageBlock entity={turn.userMessage} isStuck={isStuck} />
        {isStuck && (
          <div
            style={{
              position: 'absolute',
              left: 0,
              right: 0,
              bottom: -16,
              height: 16,
              background: 'linear-gradient(to bottom, hsl(var(--background)), transparent)',
              pointerEvents: 'none',
            }}
          />
        )}
      </div>

      {turn.blocks.length > 0 && (
        <div
          className="flex flex-col"
          style={{
            gap: 'var(--conversation-gap-block)',
            padding: '10px 16px 0 16px',
          }}
        >
          {turn.blocks.map((block, i) => (
            <BlockRenderer key={i} block={block} multiAgent={multiAgentTurn} />
          ))}
        </div>
      )}
    </div>
  )
}
