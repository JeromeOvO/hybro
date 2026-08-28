import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { cleanup, render, screen } from '../../../utils/test-utils'
import { ConversationMessageList } from '@/components/conversation/ConversationMessageList'
import { useMessageStore } from '@/stores/message-store'
import { useRoomUiStore } from '@/stores/room-ui-store'
import { useTurnStore } from '@/stores/turn-store'

describe('ConversationMessageList optimistic Turn', () => {
  beforeEach(() => {
    useMessageStore.getState().clearRoom()
    useMessageStore.getState().setRoom('room-1')
    useTurnStore.getState().clear()
    useRoomUiStore.getState().resetAll()
  })

  afterEach(cleanup)

  it('renders idle legacy room history when no canonical root exists', () => {
    useMessageStore.getState().upsertMessage({
      id: 'user-legacy', roomId: 'room-1', messageType: 'user',
      content: 'Weather?', senderName: 'You',
      timestamp: '2030-01-01T00:00:00.000Z', clientRequestId: 'client-legacy',
    }, 'db')
    useMessageStore.getState().upsertMessage({
      id: 'agent-legacy', roomId: 'room-1', messageType: 'agent',
      content: 'Sunny', senderName: 'Weather Agent',
      timestamp: '2030-01-01T00:00:01.000Z', clientRequestId: 'client-legacy',
      relatedMessageId: 'user-legacy', taskStatus: 'completed',
    }, 'db')

    render(<ConversationMessageList roomId="room-1" />)

    expect(screen.getByText('Weather?')).toBeInTheDocument()
    expect(screen.getByRole('status', { name: 'Weather Agent — Completed' })).toBeInTheDocument()
  })

  it('keeps orphaned HITL prompts out of the conversation body', () => {
    useMessageStore.getState().upsertMessage({
      id: 'hitl-message-1',
      roomId: 'room-1',
      messageType: 'agent',
      content: '',
      senderName: 'Cyber Broker Agent',
      timestamp: '2030-01-01T00:00:01.000Z',
      taskStatus: 'input-required',
      hitlRequestId: 'cloud-providers',
      hitlInteractionId: 'interaction-1',
      hitlPrompt: 'Which cloud providers do you use?',
      hitlPromptType: 'text',
      hitlGroupIndex: 0,
      hitlGroupTotal: 1,
      hitlResolved: false,
    }, 'sse')

    render(<ConversationMessageList roomId="room-1" />)

    expect(screen.queryByText('Unattributed responses')).not.toBeInTheDocument()
    expect(screen.queryByText('Which cloud providers do you use?')).not.toBeInTheDocument()
  })

  it('shows the live Turn Trace before canonical run_started arrives', () => {
    useMessageStore.getState().upsertMessage({
      id: 'cr:client-1',
      roomId: 'room-1',
      messageType: 'user',
      content: 'Plan my trip',
      senderName: 'You',
      timestamp: '2030-01-01T00:00:00.000Z',
      clientRequestId: 'client-1',
      processingStatusLogs: [{
        id: 'processing-1',
        message: 'Processing your request',
        timestamp: '2030-01-01T00:00:00.001Z',
      }],
    }, 'optimistic')
    useRoomUiStore.getState().setProcessing('room-1', true)

    render(<ConversationMessageList roomId="room-1" />)

    expect(screen.getByText('Plan my trip')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /^Running,/ })).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByText('Preparing a response')).toBeInTheDocument()
  })
})
