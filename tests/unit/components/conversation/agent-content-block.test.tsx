import { describe, expect, it } from 'vitest'
import { render, screen } from '../../../utils/test-utils'
import { AgentContentBlock } from '@/components/conversation/AgentContentBlock'

describe('AgentContentBlock', () => {
  it('does not render text-only stream artifacts when their text is already promoted to content', () => {
    const { container } = render(
      <AgentContentBlock
        agentId="agent-1"
        agentName="Hello World Agent"
        content="Hello World"
        isStreaming={false}
        artifacts={[
          {
            artifactId: 'message-1-stream',
            name: 'streaming-multimodal',
            parts: [{ kind: 'text', text: 'Hello World' }],
            isStreaming: false,
          },
        ]}
      />
    )

    expect(screen.getAllByText('Hello World')).toHaveLength(1)
    expect(container.querySelector('.conversation-content-body')).toBeTruthy()
    expect(container.querySelector('.conversation-markdown-body')).toBeTruthy()
  })
})
