import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { RoomChatInput, MAX_MESSAGE_LENGTH } from '@/components/room-chat-input'

vi.mock('@/components/group-selector', () => ({
  GroupSelector: () => <div data-testid="group-selector" />,
}))

// jsdom doesn't implement URL.createObjectURL / revokeObjectURL
if (!globalThis.URL.createObjectURL) {
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
}
if (!globalThis.URL.revokeObjectURL) {
  globalThis.URL.revokeObjectURL = vi.fn()
}

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
      expect(screen.getAllByTestId('send-button').length).toBeGreaterThan(0)
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
      expect(screen.getByTestId('sending-button')).toBeTruthy()
    })

    it('should show stop button when processing=true', () => {
      renderInput({ processing: true })
      expect(screen.getByTestId('stop-processing')).toBeTruthy()
    })

    it('should show cancelling spinner when processing and cancelling', () => {
      renderInput({ processing: true, cancelling: true })
      expect(screen.getByTestId('cancelling-button')).toBeTruthy()
    })

    it('should call onCancel when stop button is clicked', () => {
      const onCancel = vi.fn()
      renderInput({ processing: true, onCancel })
      fireEvent.click(screen.getByTestId('stop-processing'))
      expect(onCancel).toHaveBeenCalledOnce()
    })
  })

  describe('submit', () => {
    it('should not submit when message is empty', () => {
      const onSubmit = vi.fn()
      renderInput({ onSubmit })
      const sendBtns = screen.getAllByTestId('send-button')
      fireEvent.click(sendBtns[0])
      expect(onSubmit).not.toHaveBeenCalled()
    })

    it('should not submit when disableSend=true', () => {
      const onSubmit = vi.fn()
      const { container } = renderInput({ onSubmit, disableSend: true })

      const editor = container.querySelector('[contenteditable]') as HTMLElement
      editor.textContent = 'Hello'
      fireEvent.input(editor)

      const sendBtns = screen.getAllByTestId('send-button')
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
      expect(container.querySelector('[data-testid="expand-editor"]')).toBeNull()
    })
  })

  describe('message size validation', () => {
    function setEditorText(container: HTMLElement, text: string) {
      const editor = container.querySelector('[contenteditable]') as HTMLElement
      editor.textContent = text
      fireEvent.input(editor)
      return editor
    }

    it('should not show counter for short messages', () => {
      const { container } = renderInput()
      setEditorText(container, 'Hello world')
      expect(screen.queryByTestId('char-counter')).toBeNull()
    })

    it('should show counter when approaching limit', () => {
      const { container } = renderInput()
      const text = 'a'.repeat(MAX_MESSAGE_LENGTH - 400)
      setEditorText(container, text)
      const counter = screen.getByTestId('char-counter')
      expect(counter).toBeTruthy()
      expect(counter.textContent).toContain(MAX_MESSAGE_LENGTH.toLocaleString())
    })

    it('should disable send button when over limit', () => {
      const { container } = renderInput()
      const text = 'a'.repeat(MAX_MESSAGE_LENGTH + 1)
      setEditorText(container, text)
      const sendBtn = screen.getByTestId('send-button')
      expect(sendBtn.hasAttribute('disabled')).toBe(true)
    })

    it('should apply red styling when over limit', () => {
      const { container } = renderInput()
      const text = 'a'.repeat(MAX_MESSAGE_LENGTH + 1)
      setEditorText(container, text)
      const counter = screen.getByTestId('char-counter')
      expect(counter.className).toContain('text-red-600')
    })

    it('should apply amber styling when near but under warning threshold', () => {
      const { container } = renderInput()
      const text = 'a'.repeat(MAX_MESSAGE_LENGTH - 400)
      setEditorText(container, text)
      const counter = screen.getByTestId('char-counter')
      expect(counter.className).toContain('text-amber-600')
    })
  })

  describe('attachment limits', () => {
    it('should cap attachments at MAX_ATTACHMENTS (10)', () => {
      const createSpy = vi.spyOn(globalThis.URL, 'createObjectURL')
      createSpy.mockReturnValue('blob:mock')

      const { container } = renderInput()
      const dropZone = container.querySelector('.group\\/input') || container.firstElementChild!

      const files = Array.from({ length: 15 }, (_, i) =>
        new File([`data-${i}`], `file-${i}.png`, { type: 'image/png' })
      )

      fireEvent.drop(dropZone, {
        dataTransfer: { files, types: ['Files'] },
      })

      expect(createSpy).toHaveBeenCalledTimes(10)
      createSpy.mockRestore()
    })
  })
})
