import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactList } from '@/components/artifact-list'
import type { ArtifactData } from '@/stores/message-store/types'

interface AgentContentBlockProps {
  agentId: string
  agentName: string
  content: string
  isStreaming: boolean
  showAttribution?: boolean
  artifacts?: ArtifactData[]
}

export function AgentContentBlock({ agentName, content, isStreaming, showAttribution, artifacts }: AgentContentBlockProps) {
  return (
    <div>
      {showAttribution && (
        <div className="text-xs mb-1" style={{ color: 'var(--conversation-text-muted)' }}>
          {agentName}:
        </div>
      )}
      <div className={isStreaming ? 'conversation-streaming-cursor' : ''} style={{ lineHeight: 1.8, fontSize: 14 }}>
        <MarkdownContent content={content} isStreaming={isStreaming} />
      </div>
      {artifacts && artifacts.length > 0 && (
        <ArtifactList artifacts={artifacts} />
      )}
    </div>
  )
}
