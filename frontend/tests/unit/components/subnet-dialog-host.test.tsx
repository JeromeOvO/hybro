import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '../../utils/test-utils'
import userEvent from '@testing-library/user-event'
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup } from '@/lib/types/agent-group'

const createAgentGroup = vi.fn()
const updateAgentGroup = vi.fn()
const deleteAgentGroup = vi.fn()
vi.mock('@/lib/api/agent-group', () => ({
  createAgentGroup: (...a: unknown[]) => createAgentGroup(...a),
  updateAgentGroup: (...a: unknown[]) => updateAgentGroup(...a),
  deleteAgentGroup: (...a: unknown[]) => deleteAgentGroup(...a),
}))
vi.mock('@/components/ui/banner', () => ({ banner: { success: vi.fn(), error: vi.fn() } }))

import { SubnetDialogHost } from '@/components/subnets/subnet-dialog-host'

// Radix Checkbox measures its hidden input with ResizeObserver, which jsdom lacks.
vi.stubGlobal('ResizeObserver', class {
  observe() {}
  unobserve() {}
  disconnect() {}
})

afterEach(cleanup)

const agent = { agent_id: 'a', agent_status: 'active', agent_card: { name: 'writer', description: '', skills: [] } } as unknown as Agent
const group = { group_id: 'g', name: 'Research', description: '', agents: ['a'], type: 'user', owner_id: 'u' } as AgentGroup
const base = { open: true, onOpenChange: vi.fn(), availableAgents: [agent], loadingAgents: false, userId: 'u', onSaved: vi.fn(), onDeleted: vi.fn() }

describe('SubnetDialogHost', () => {
  it('creates a subnet with subnet terminology', async () => {
    createAgentGroup.mockResolvedValue({ success: true, group: { ...group, group_id: 'new', name: 'Ops', agents: [] } })
    render(<SubnetDialogHost {...base} action={{ type: 'create' }} />)
    expect(screen.getByRole('heading', { name: 'Create subnet' })).toBeInTheDocument()
    await userEvent.type(screen.getByLabelText('Name'), 'Ops')
    await userEvent.click(screen.getByRole('button', { name: 'Create subnet' }))
    await waitFor(() => expect(base.onSaved).toHaveBeenCalledWith(expect.objectContaining({ group_id: 'new' }), 'create'))
    expect(createAgentGroup).toHaveBeenCalledWith({ owner_id: 'u', name: 'Ops', description: '', agents: [] }, undefined)
  })

  it('edits only changed fields and keeps membership when untouched', async () => {
    updateAgentGroup.mockResolvedValue({ success: true, group: { ...group, name: 'Research 2' } })
    render(<SubnetDialogHost {...base} action={{ type: 'edit', group }} />)
    await userEvent.clear(screen.getByLabelText('Name'))
    await userEvent.type(screen.getByLabelText('Name'), 'Research 2')
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }))
    await waitFor(() => expect(updateAgentGroup).toHaveBeenCalledWith({ group_id: 'g', name: 'Research 2', description: '' }, undefined))
  })

  it('confirms deletion with a solid danger button', async () => {
    deleteAgentGroup.mockResolvedValue({ success: true })
    render(<SubnetDialogHost {...base} action={{ type: 'delete', group }} />)
    const confirm = screen.getByRole('button', { name: 'Delete subnet' })
    expect(confirm.className).toMatch(/(^|\s)bg-destructive(\s|$)/)
    await userEvent.click(confirm)
    await waitFor(() => expect(base.onDeleted).toHaveBeenCalledWith(group))
  })
})
