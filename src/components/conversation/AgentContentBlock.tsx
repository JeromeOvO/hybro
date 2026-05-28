import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactList } from '@/components/artifact-list'
import type { ArtifactData } from '@/stores/message-store/types'

interface AgentContentBlockProps {
  agentId: string
  agentName: string
  messageId?: string
  content: string
  isStreaming: boolean
  showAttribution?: boolean
  artifacts?: ArtifactData[]
}

function textOnlyArtifactContent(artifact: ArtifactData): string | null {
  if (artifact.parts.length === 0) return null
  if (!artifact.parts.every(part => part.kind === 'text')) return null
  return artifact.parts.map(part => part.text ?? '').join('').trim()
}

export function AgentContentBlock({ agentId, agentName, messageId, content, isStreaming, showAttribution, artifacts }: AgentContentBlockProps) {
  const normalizedContent = content.trim()
  const displayArtifacts = artifacts?.filter(artifact => {
    if (!normalizedContent) return true
    return textOnlyArtifactContent(artifact) !== normalizedContent
  })

  return (
    <div data-quote-message-id={messageId} data-quote-agent-name={agentName} data-quote-source-kind="agent" data-quote-agent-id={agentId}>
      {showAttribution && (
        <div className="text-xs mb-1" style={{ color: 'var(--conversation-text-muted)' }}>
          {agentName}:
        </div>
      )}
      <div className={`conversation-content-body ${isStreaming ? 'conversation-streaming-cursor' : ''}`}>
        <MarkdownContent className="conversation-markdown-body" content={content} isStreaming={isStreaming} />
      </div>
      {displayArtifacts && displayArtifacts.length > 0 && (
        <ArtifactList artifacts={displayArtifacts} />
      )}
    </div>
  )
}
