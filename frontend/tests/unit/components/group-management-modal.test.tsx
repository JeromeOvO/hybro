import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { GroupManagementModal } from '@/components/group-management-modal'

afterEach(() => {
  cleanup()
})

describe('GroupManagementModal team terminology', () => {
  it('uses team terminology in the saved-team list and creation form', async () => {
    const user = userEvent.setup()
    render(
      <GroupManagementModal
        open
        onOpenChange={vi.fn()}
        groups={[]}
        onGroupsChange={vi.fn()}
        availableAgents={[]}
        loadingAgents={false}
        userId="user-1"
      />,
    )

    expect(screen.getByText('Manage Saved Teams')).toBeInTheDocument()
    expect(screen.getByText('No saved teams yet')).toBeInTheDocument()
    expect(screen.queryByText('Manage Saved Groups')).not.toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Create New Team' }))

    expect(screen.getByRole('heading', { name: 'Create New Team' })).toBeInTheDocument()
    expect(screen.getByText('Team Name')).toBeInTheDocument()
    expect(screen.queryByText('Description (optional)')).not.toBeInTheDocument()
    expect(screen.queryByPlaceholderText('What is this team for?')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create Team' })).toBeInTheDocument()
    expect(screen.queryByText('Group Name')).not.toBeInTheDocument()
  })
})
