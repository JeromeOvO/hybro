import { X } from 'lucide-react'
import { MarkdownContent } from '@/components/markdown-content'
import { ArtifactList } from '@/components/artifact-list'
import { getAgentAvatarUri } from '@/lib/agent-avatar'
import type { AgentDisplayProps, AgentResponseDetail } from '@/lib/selectors/conversation-types'

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

function AgentResponseDetailHeader({ detail, onClose }: AgentResponseDetailPaneProps) {
  const toneColors: Record<AgentDisplayProps['tone'], string> = {
    accent: 'rgb(0, 255, 255)',
    muted: 'var(--conversation-agent-green)',
    danger: 'var(--conversation-danger)',
    warning: 'var(--conversation-agent-yellow)',
  }

  return (
    <div
      className="conversation-detail-agent-header"
      data-testid="agent-response-detail-header"
      style={{ backgroundColor: detail.theme.cardBg }}
    >
      <div
        className="conversation-detail-agent-avatar"
        style={{ backgroundColor: detail.theme.avatarLightBg }}
      >
        <img src={getAgentAvatarUri(detail.agentId)} alt="" />
      </div>

      <div className="conversation-detail-agent-main">
        <div className="conversation-detail-agent-name">
          {detail.agentName}
        </div>
        {detail.taskDescription && (
          <div className="conversation-detail-agent-task">
            <span className="conversation-detail-agent-branch">&#x2514;</span>
            <span className="conversation-detail-agent-task-text">
              {detail.taskDescription}
            </span>
          </div>
        )}
      </div>

      <span
        className="conversation-detail-status-pill"
        role="status"
        aria-label={detail.display.ariaLabel}
        style={{ color: toneColors[detail.display.tone] }}
      >
        {detail.display.label}
      </span>
      <button
        type="button"
        aria-label="Close agent response"
        className="conversation-detail-close-button"
        onClick={onClose}
      >
        <X className="h-4 w-4" />
      </button>
    </div>
  )
}

export function AgentResponseDetailPane({ detail, onClose }: AgentResponseDetailPaneProps) {
  const hasContent = detail.content.trim().length > 0
  const hasArtifacts = (detail.artifacts?.length ?? 0) > 0

  return (
    <aside className="conversation-detail-pane" data-testid="agent-response-detail-pane" aria-label="Agent response detail">
      <div className="conversation-detail-sticky" data-testid="agent-response-detail-sticky">
        <AgentResponseDetailHeader detail={detail} onClose={onClose} />
      </div>

      <div className="conversation-detail-body">
        <div className="conversation-detail-frame">
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
      </div>
    </aside>
  )
}
