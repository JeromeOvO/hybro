import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ComponentProps } from 'react'
import { AddToSubnetMenu } from '@/components/networks/add-to-subnet-menu'
import type { AgentGroup } from '@/lib/types/agent-group'

vi.mock('@/components/ui/banner', () => ({ banner: { success: vi.fn(), error: vi.fn() } }))

afterEach(cleanup)

const group = (id: string, agents: string[]) => ({ group_id: id, name: id, agents, type: 'user', owner_id: 'u' }) as AgentGroup

async function open(props: Partial<ComponentProps<typeof AddToSubnetMenu>> = {}) {
  const onSetMembers = vi.fn().mockResolvedValue(undefined)
  const onCreateWithAgent = vi.fn()
  render(<AddToSubnetMenu agentId="a" networks={[group('Research', ['a', 'b']), group('Travel', ['b'])]} pending={false} onSetMembers={onSetMembers} onCreateWithAgent={onCreateWithAgent} {...props} />)
  await userEvent.click(screen.getByRole('button', { name: 'Add to subnet' }))
  return { onSetMembers, onCreateWithAgent }
}

describe('AddToSubnetMenu', () => {
  it('checks current memberships', async () => {
    await open()
    expect(screen.getByRole('menuitemcheckbox', { name: 'Research' })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByRole('menuitemcheckbox', { name: 'Travel' })).toHaveAttribute('aria-checked', 'false')
  })

  it('adds and removes using the latest cached members', async () => {
    const { onSetMembers } = await open()
    await userEvent.click(screen.getByRole('menuitemcheckbox', { name: 'Travel' }))
    expect(onSetMembers).toHaveBeenCalledWith('Travel', ['b', 'a'])
    await userEvent.click(screen.getByRole('button', { name: 'Add to subnet' }))
    await userEvent.click(screen.getByRole('menuitemcheckbox', { name: 'Research' }))
    expect(onSetMembers).toHaveBeenCalledWith('Research', ['b'])
  })

  it('disables items while a write is pending', async () => {
    await open({ pending: true })
    expect(screen.getByRole('menuitemcheckbox', { name: 'Travel' })).toHaveAttribute('aria-disabled', 'true')
  })

  it('offers Retry after a failure', async () => {
    const onSetMembers = vi.fn().mockRejectedValueOnce(new Error('boom')).mockResolvedValue(undefined)
    await open({ onSetMembers })
    await userEvent.click(screen.getByRole('menuitemcheckbox', { name: 'Travel' }))
    await userEvent.click(screen.getByRole('button', { name: 'Add to subnet' }))
    await userEvent.click(await screen.findByRole('menuitem', { name: "Couldn't update Travel. Retry" }))
    await waitFor(() => expect(onSetMembers).toHaveBeenCalledTimes(2))
  })

  it('shows only Create subnet when there are no subnets', async () => {
    const { onCreateWithAgent } = await open({ networks: [] })
    expect(screen.queryByRole('menuitemcheckbox')).toBeNull()
    await userEvent.click(screen.getByRole('menuitem', { name: 'Create subnet…' }))
    expect(onCreateWithAgent).toHaveBeenCalled()
  })
})
