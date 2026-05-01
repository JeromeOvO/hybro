import { X } from 'lucide-react'
import { AgentCard } from './AgentCard'
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactList } from '@/components/artifact-list'
import type { AgentResponseDetail } from '@/lib/selectors/conversation-types'

interface AgentResponseDetailPaneProps {
  detail: AgentResponseDetail
  onClose: () => void
}

function EmptyResponse({ detail }: { detail: AgentResponseDetail }) {
  const message = detail.taskError || detail.taskStatusMessage || 'No response content yet.'
  return (
    <div className="text-sm" style={{ color: 'var(--conversation-text-muted)' }}>
      {message}
    </div>
  )
}

export function AgentResponseDetailPane({ detail, onClose }: AgentResponseDetailPaneProps) {
  const hasContent = detail.content.trim().length > 0
  const hasArtifacts = (detail.artifacts?.length ?? 0) > 0

  return (
    <aside className="conversation-detail-pane" data-testid="agent-response-detail-pane" aria-label="Agent response detail">
      <div className="conversation-detail-sticky" data-testid="agent-response-detail-sticky">
        <div className="conversation-detail-header-row">
          <div className="min-w-0 flex-1">
            <AgentCard
              messageId={detail.messageId}
              agentId={detail.agentId}
              agentName={detail.agentName}
              taskDescription={detail.taskDescription}
              theme={detail.theme}
              display={detail.display}
              interactive={false}
              selected
            />
          </div>
          <button
            type="button"
            aria-label="Close agent response"
            className="conversation-detail-close-button"
            onClick={onClose}
          >
            <X className="h-4 w-4" />
          </button>
        </div>
      </div>

      <div className="conversation-detail-body">
        <section className="conversation-detail-response" aria-label="Agent response">
          {hasContent ? (
            <div className={`conversation-content-body ${detail.isStreaming ? 'conversation-streaming-cursor' : ''}`}>
              <MarkdownContent className="conversation-markdown-body" content={detail.content} isStreaming={detail.isStreaming} />
            </div>
          ) : (
            <EmptyResponse detail={detail} />
          )}
          {hasArtifacts && (
            <ArtifactList artifacts={detail.artifacts!} />
          )}
        </section>
      </div>
    </aside>
  )
}
