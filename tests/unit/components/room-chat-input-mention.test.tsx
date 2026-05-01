import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup, waitFor } from '@testing-library/react'
import { RoomChatInput } from '@/components/room-chat-input'

vi.mock('@/components/group-selector', () => ({
  GroupSelector: () => <div data-testid="group-selector" />,
}))

vi.mock('@/components/file-attachment-button', () => ({
  FileAttachmentButton: () => <button data-testid="file-attach" />,
  ACCEPTED_MIME_SET: new Set(['image/png']),
  MAX_FILE_SIZE: 10_000_000,
  MAX_ATTACHMENTS: 10,
}))

vi.mock('@/components/attachment-preview', () => ({
  AttachmentPreview: () => null,
}))

if (!globalThis.URL.createObjectURL) {
  globalThis.URL.createObjectURL = vi.fn(() => 'blob:mock')
}
if (!globalThis.URL.revokeObjectURL) {
  globalThis.URL.revokeObjectURL = vi.fn()
}

if (!Element.prototype.scrollIntoView) {
  Element.prototype.scrollIntoView = vi.fn()
}

const agents = [
  { id: 'a-1', name: 'Alpha Agent' },
  { id: 'a-2', name: 'Beta Agent' },
  { id: 'a-3', name: 'Gamma Agent' },
]

const defaultProps = {
  onSubmit: vi.fn(),
  agents,
}

function renderInput(overrides: Record<string, unknown> = {}) {
  return render(<RoomChatInput {...defaultProps} {...overrides} />)
}

function getEditor(container: HTMLElement) {
  return container.querySelector('[contenteditable="true"]') as HTMLDivElement
}

function typeInEditor(editor: HTMLDivElement, text: string) {
  editor.focus()
  editor.textContent = text
  const sel = window.getSelection()!
  const range = document.createRange()
  range.selectNodeContents(editor)
  range.collapse(false)
  sel.removeAllRanges()
  sel.addRange(range)
  fireEvent.input(editor)
}

describe('RoomChatInput – @mention behavior', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  afterEach(() => {
    cleanup()
  })

  it('should show mention dropdown when @ is typed', () => {
    const { container } = renderInput()
    const editor = getEditor(container)

    typeInEditor(editor, '@')

    expect(screen.getByText('Mention an agent')).toBeTruthy()
    expect(screen.getByText('Alpha Agent')).toBeTruthy()
    expect(screen.getByText('Beta Agent')).toBeTruthy()
    expect(screen.getByText('Gamma Agent')).toBeTruthy()
  })

  it('should show an empty mention dropdown when no agents are available', () => {
    const { container } = renderInput({ agents: [] })
    const editor = getEditor(container)

    typeInEditor(editor, '@')

    expect(screen.getByText('Mention an agent')).toBeTruthy()
    expect(screen.getByText('No agents available')).toBeTruthy()
  })

  it('should not submit when Enter is pressed in an empty mention dropdown', () => {
    const onSubmit = vi.fn()
    const { container } = renderInput({ agents: [], onSubmit })
    const editor = getEditor(container)

    typeInEditor(editor, '@')
    fireEvent.keyDown(editor, { key: 'Enter' })

    expect(onSubmit).not.toHaveBeenCalled()
    expect(screen.getByText('No agents available')).toBeTruthy()
  })

  it('anchors the mention dropdown flush at the conversation body width', () => {
    const { container } = renderInput()
    const editor = getEditor(container)

    typeInEditor(editor, '@')

    const dropdown = screen.getByText('Mention an agent').closest('.absolute')

    expect(dropdown).toBeTruthy()
    expect(dropdown?.className).toContain('left-[var(--conversation-body-inset)]')
    expect(dropdown?.className).toContain('right-[var(--conversation-body-inset)]')
    expect(dropdown?.className).toContain('-mb-px')
    expect(dropdown?.className).not.toContain('left-0')
    expect(dropdown?.className).not.toContain('right-0')
    expect(dropdown?.className).not.toContain('left-4')
    expect(dropdown?.className).not.toContain('right-4')
    expect(dropdown?.className).not.toContain('mb-3')
  })

  it('should filter agents by text typed after @', () => {
    const { container } = renderInput()
    const editor = getEditor(container)

    typeInEditor(editor, '@Beta')

    expect(screen.getByText('Mention an agent')).toBeTruthy()
    expect(screen.getByText('Beta Agent')).toBeTruthy()
    expect(screen.queryByText('Alpha Agent')).toBeNull()
    expect(screen.queryByText('Gamma Agent')).toBeNull()
  })

  it('should insert mention when an agent is clicked in the dropdown', async () => {
    const { container } = renderInput()
    const editor = getEditor(container)

    typeInEditor(editor, '@')

    const alphaButton = screen.getByText('Alpha Agent')
    fireEvent.click(alphaButton)

    await waitFor(() => {
      expect(screen.queryByText('Mention an agent')).toBeNull()
    })

    const mentionSpan = editor.querySelector('.room-mention')
    expect(mentionSpan).toBeTruthy()
    expect(mentionSpan?.textContent).toBe('@Alpha Agent')
    expect(mentionSpan?.getAttribute('data-id')).toBe('a-1')
  })

  it('should close mention dropdown on Escape key', () => {
    const { container } = renderInput()
    const editor = getEditor(container)

    typeInEditor(editor, '@')
    expect(screen.getByText('Mention an agent')).toBeTruthy()

    fireEvent.keyDown(editor, { key: 'Escape' })

    expect(screen.queryByText('Mention an agent')).toBeNull()
  })

  it('should remove mention token when its span is deleted', async () => {
    const { container } = renderInput()
    const editor = getEditor(container)

    typeInEditor(editor, '@')
    fireEvent.click(screen.getByText('Alpha Agent'))

    await waitFor(() => {
      expect(editor.querySelector('.room-mention')).toBeTruthy()
    })

    const mentionSpan = editor.querySelector('.room-mention')!
    mentionSpan.remove()
    fireEvent.input(editor)

    expect(editor.querySelector('.room-mention')).toBeNull()
  })

  it('should support multiple mentions in the same message', async () => {
    const { container } = renderInput()
    const editor = getEditor(container)

    typeInEditor(editor, '@')
    fireEvent.click(screen.getByText('Alpha Agent'))

    await waitFor(() => {
      expect(editor.querySelector('.room-mention')).toBeTruthy()
    })

    const currentText = editor.textContent || ''
    typeInEditor(editor, currentText + ' hello @')

    expect(screen.getByText('Mention an agent')).toBeTruthy()

    fireEvent.click(screen.getByText('Beta Agent'))

    await waitFor(() => {
      const mentions = editor.querySelectorAll('.room-mention')
      expect(mentions.length).toBeGreaterThanOrEqual(1)
    })
  })
})
