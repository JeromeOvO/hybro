import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { GroupSelector } from '@/components/group-selector'

describe('GroupSelector', () => {
  it('keeps mentioned agent display to one compact truncated line', () => {
    render(
      <GroupSelector
        selectedGroup="all_agents"
        onGroupChange={vi.fn()}
        groups={[]}
        mentionedAgents={[{ id: 'a-1', name: 'Hello World Agent With A Long Name' }]}
        className="min-w-0 max-w-[11rem]"
      />,
    )

    const label = screen.getByText('@Hello World Agent With A Long Name')
    const pill = label.closest('div')

    expect(pill?.className).toContain('h-8')
    expect(pill?.className).toContain('min-w-0')
    expect(pill?.className).toContain('max-w-full')
    expect(pill?.className).toContain('overflow-hidden')
    expect(pill?.className).toContain('whitespace-nowrap')
    expect(label.className).toContain('min-w-0')
    expect(label.className).toContain('truncate')
  })
})
