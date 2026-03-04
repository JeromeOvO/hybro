import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { RoomChatInput } from '@/components/room-chat-input'

vi.mock('@/components/group-selector', () => ({
  GroupSelector: () => <div data-testid="group-selector" />,
}))

const defaultProps = {
  onSubmit: vi.fn(),
  agents: [
    { id: 'a-1', name: 'Alpha Agent' },
    { id: 'a-2', name: 'Beta Agent' },
  ],
}

function renderInput(overrides: Record<string, unknown> = {}) {
  return render(<RoomChatInput {...defaultProps} {...overrides} />)
}

describe('RoomChatInput', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  describe('rendering', () => {
    it('should render contenteditable editor', () => {
      const { container } = renderInput()
      const editor = container.querySelector('[contenteditable="true"]')
      expect(editor).toBeTruthy()
    })

    it('should render send button', () => {
      renderInput()
      expect(screen.getAllByTitle('Send message (Enter)').length).toBeGreaterThan(0)
    })

    it('should render group selector by default', () => {
      renderInput()
      expect(screen.getAllByTestId('group-selector').length).toBeGreaterThan(0)
    })

    it('should hide group selector when showGroupSelector=false', () => {
      renderInput({ showGroupSelector: false })
      expect(screen.queryByTestId('group-selector')).toBeNull()
    })

    it('should disable editor when disabled=true', () => {
      const { container } = renderInput({ disabled: true })
      const editor = container.querySelector('[contenteditable="false"]')
      expect(editor).toBeTruthy()
    })
  })

  describe('button states', () => {
    it('should show spinner when sending=true', () => {
      renderInput({ sending: true })
      expect(screen.getByTitle('Sending message...')).toBeTruthy()
    })

    it('should show stop button when processing=true', () => {
      renderInput({ processing: true })
      expect(screen.getByTitle('Stop processing')).toBeTruthy()
    })

    it('should show cancelling spinner when processing and cancelling', () => {
      renderInput({ processing: true, cancelling: true })
      expect(screen.getByTitle('Cancelling...')).toBeTruthy()
    })

    it('should call onCancel when stop button is clicked', () => {
      const onCancel = vi.fn()
      renderInput({ processing: true, onCancel })
      fireEvent.click(screen.getByTitle('Stop processing'))
      expect(onCancel).toHaveBeenCalledOnce()
    })
  })

  describe('submit', () => {
    it('should not submit when message is empty', () => {
      const onSubmit = vi.fn()
      renderInput({ onSubmit })
      const sendBtns = screen.getAllByTitle('Send message (Enter)')
      fireEvent.click(sendBtns[0])
      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('should not submit when disableSend=true', () => {
      const onSubmit = vi.fn()
      const { container } = renderInput({ onSubmit, disableSend: true })

      const editor = container.querySelector('[contenteditable]') as HTMLElement
      editor.textContent = 'Hello'
      fireEvent.input(editor)

      const sendBtns = screen.getAllByTitle('Send message (Enter)')
      fireEvent.click(sendBtns[0])

      expect(onSubmit).not.toHaveBeenCalled()
    })
  })

  describe('quote preview', () => {
    it('should show quote preview when quote prop is set', () => {
      renderInput({
        quote: { messageId: 'q-1', content: 'Quoted text', senderName: 'Alice' },
      })
      expect(screen.getByText('Quoted text')).toBeTruthy()
      expect(screen.getByText('Alice')).toBeTruthy()
    })

    it('should not show quote when quote is null', () => {
      renderInput({ quote: null })
      expect(screen.queryByLabelText('Remove quote')).toBeNull()
    })

    it('should call onClearQuote when remove button is clicked', () => {
      const onClearQuote = vi.fn()
      renderInput({
        quote: { messageId: 'q-1', content: 'text', senderName: 'Bob' },
        onClearQuote,
      })
      fireEvent.click(screen.getByLabelText('Remove quote'))
      expect(onClearQuote).toHaveBeenCalledOnce()
    })
  })

  describe('expand/collapse', () => {
    it('should not show expand button when content is not overflowing', () => {
      const { container } = renderInput()
      expect(container.querySelector('[title="Expand editor"]')).toBeNull()
    })
  })
})
