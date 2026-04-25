import { describe, it, expect, vi } from 'vitest'
import { render, screen, fireEvent } from '@testing-library/react'
import { UserInputBlock } from '@/components/turn/UserInputBlock'

vi.mock('@clerk/nextjs', () => ({
  useUser: () => ({
    user: {
      firstName: 'Test',
      username: 'test-user',
      imageUrl: null,
    },
  }),
}))

describe('UserInputBlock', () => {
  it('copies mentions using mention token MIME', () => {
    render(
      <UserInputBlock
        data={{
          text: 'hello <@agent-123|Planner>',
          attachments: [],
        }}
      />
    )

    const mentionLink = screen.getByRole('link', { name: '@Planner' })
    const bubble = mentionLink.closest('div.rounded-xl') as HTMLElement
    expect(bubble).toBeTruthy()

    const selection = window.getSelection()
    const range = document.createRange()
    range.selectNodeContents(mentionLink)
    selection?.removeAllRanges()
    selection?.addRange(range)

    const clipboardData = {
      setData: vi.fn(),
      getData: vi.fn(() => ''),
      types: [],
    }

    fireEvent.copy(bubble, { clipboardData })

    expect(clipboardData.setData).toHaveBeenCalledWith('text/plain', '@Planner')
    expect(clipboardData.setData).toHaveBeenCalledWith('application/x-hybro-mentions', '<@agent-123|Planner>')
    selection?.removeAllRanges()
  })
})
