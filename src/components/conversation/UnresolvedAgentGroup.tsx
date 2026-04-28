import type { ConversationBlock } from '@/lib/selectors/conversation-types'
import { AgentCard } from './AgentCard'
import { AgentContentBlock } from './AgentContentBlock'

interface UnresolvedAgentGroupProps {
  blocks: ConversationBlock[]
}

export function UnresolvedAgentGroup({ blocks }: UnresolvedAgentGroupProps) {
  return (
    <div style={{ padding: '0 var(--conversation-padding-inner)' }}>
      <div className="text-xs font-medium mb-2" style={{ color: 'var(--conversation-text-muted)' }}>
        Unattributed responses
      </div>
      <div className="flex flex-col" style={{ gap: 'var(--conversation-gap-block)' }}>
        {blocks.map((block, i) => {
          if (block.type === 'agent_card') return <AgentCard key={i} {...block} />
          if (block.type === 'agent_content') return <AgentContentBlock key={i} {...block} />
          if (block.type === 'unresolved_content') {
            return (
              <div key={i} className="text-sm" style={{ color: 'var(--conversation-text-tertiary)' }}>
                {block.entity.content}
              </div>
            )
          }
          return null
        })}
      </div>
    </div>
  )
}
