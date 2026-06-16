import { describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { GroupSelector } from '@/components/group-selector'

describe('GroupSelector', () => {
  it('keeps mentioned agent display compact with avatar and count', () => {
    render(
      <GroupSelector
        selectedGroup="all_agents"
        onGroupChange={vi.fn()}
        groups={[]}
        mentionedAgents={[{ id: 'a-1', name: 'Hello World Agent With A Long Name' }]}
        className="min-w-0 max-w-[11rem]"
      />,
    )

    expect(screen.getByAltText('Hello World Agent With A Long Name')).toBeInTheDocument()
    const label = screen.getByText('1 agent')
    const pill = label.closest('div')

    expect(pill?.className).toContain('h-8')
    expect(pill?.className).toContain('min-w-0')
    expect(pill?.className).toContain('whitespace-nowrap')
    expect(label.className).toContain('text-sm')
  })

  it('shows plural count for multiple mentioned agents', () => {
    render(
      <GroupSelector
        selectedGroup="all_agents"
        onGroupChange={vi.fn()}
        groups={[]}
        mentionedAgents={[
          { id: 'a-1', name: 'Agent One' },
          { id: 'a-2', name: 'Agent Two' },
        ]}
      />,
    )

    expect(screen.getByText('2 agents')).toBeInTheDocument()
    expect(screen.getByAltText('Agent One')).toBeInTheDocument()
    expect(screen.getByAltText('Agent Two')).toBeInTheDocument()
  })
})
