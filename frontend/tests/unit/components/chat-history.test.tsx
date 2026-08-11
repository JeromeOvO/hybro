import React from 'react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

import type { RoomHistoryResponse } from '@/lib/api/room'

const mocks = vi.hoisted(() => ({
  pathname: '/room/active-room',
  push: vi.fn(),
  list: vi.fn(),
  update: vi.fn(),
  reorder: vi.fn(),
  remove: vi.fn(),
}))

vi.mock('next/navigation', () => ({
  usePathname: () => mocks.pathname,
  useRouter: () => ({ push: mocks.push }),
}))

vi.mock('next/link', () => ({
  default: ({ children, href, title, 'aria-current': ariaCurrent }: React.ComponentProps<'a'> & { prefetch?: boolean; scroll?: boolean }) => (
    <a href={String(href)} title={title} aria-current={ariaCurrent}>{children}</a>
  ),
}))

vi.mock('@/lib/api/room', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/room')>()
  return {
    ...actual,
    listRoomHistory: mocks.list,
    updateRoomHistoryItem: mocks.update,
    reorderPinnedRooms: mocks.reorder,
    deleteRoomHistoryItem: mocks.remove,
  }
})

vi.mock('sonner', () => ({
  toast: { error: vi.fn(), success: vi.fn() },
}))

import { ChatHistory } from '@/components/portal/chat-history'

const history: RoomHistoryResponse = {
  items: [
    {
      room_id: 'recent-old',
      title: 'Recent old',
      last_activity_at: '2026-08-01T00:00:00Z',
      is_pinned: false,
      pin_order: null,
      status: 'idle',
    },
    {
      room_id: 'pinned-two',
      title: 'Pinned two',
      last_activity_at: '2026-08-02T00:00:00Z',
      is_pinned: true,
      pin_order: 2,
      status: 'queued',
    },
    {
      room_id: 'active-room',
      title: 'Recent new',
      last_activity_at: '2026-08-03T00:00:00Z',
      is_pinned: false,
      pin_order: null,
      status: 'processing',
    },
    {
      room_id: 'pinned-one',
      title: 'Pinned one',
      last_activity_at: '2026-08-01T00:00:00Z',
      is_pinned: true,
      pin_order: 1,
      status: 'awaiting_input',
    },
  ],
}

let currentHistory: RoomHistoryResponse

function renderHistory() {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  })
  return render(
    <QueryClientProvider client={queryClient}>
      <ChatHistory enabled userId="owner" />
    </QueryClientProvider>,
  )
}

beforeEach(() => {
  vi.clearAllMocks()
  mocks.pathname = '/room/active-room'
  currentHistory = structuredClone(history)
  mocks.list.mockImplementation(async () => currentHistory)
  mocks.update.mockImplementation(async (roomId: string, update: { title?: string; is_pinned?: boolean }) => {
    const current = currentHistory.items.find((item) => item.room_id === roomId)!
    const next = {
      ...current,
      ...update,
      pin_order: update.is_pinned === true ? 3 : update.is_pinned === false ? null : current.pin_order,
    }
    currentHistory = {
      items: currentHistory.items.map((item) => item.room_id === roomId ? next : item),
    }
    return next
  })
  mocks.reorder.mockResolvedValue({ success: true })
  mocks.remove.mockResolvedValue({ success: true })

  if (!HTMLElement.prototype.hasPointerCapture) {
    HTMLElement.prototype.hasPointerCapture = () => false
  }
  if (!HTMLElement.prototype.setPointerCapture) {
    HTMLElement.prototype.setPointerCapture = () => undefined
  }
  if (!HTMLElement.prototype.releasePointerCapture) {
    HTMLElement.prototype.releasePointerCapture = () => undefined
  }
})

afterEach(cleanup)

describe('ChatHistory', () => {
  it('renders manual pinned order, recent activity order, compact statuses, and no new-chat control', async () => {
    renderHistory()

    await screen.findByText('Pinned one')
    const rows = screen.getAllByTestId(/^history-room-/)
    expect(rows.map((row) => row.dataset.testid)).toEqual([
      'history-room-pinned-one',
      'history-room-pinned-two',
      'history-room-active-room',
      'history-room-recent-old',
    ])
    expect(screen.getByLabelText('Needs input')).toBeInTheDocument()
    expect(screen.getByLabelText('Queued')).toBeInTheDocument()
    expect(screen.getByLabelText('Working')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /new chat/i })).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Recent new' })).toHaveAttribute(
      'aria-current',
      'page',
    )
  })

  it('collapses and expands the history list from its section header', async () => {
    const user = userEvent.setup()
    renderHistory()

    await screen.findByText('Pinned one')
    const trigger = screen.getByRole('button', { name: /chat history/i })
    expect(trigger).toHaveAttribute('aria-expanded', 'true')

    await user.click(trigger)
    expect(trigger).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByText('Pinned one')).not.toBeInTheDocument()

    await user.click(trigger)
    expect(await screen.findByText('Pinned one')).toBeInTheDocument()
  })

  it('renames a room inline and can pin it from the row menu', async () => {
    const user = userEvent.setup()
    renderHistory()
    const row = await screen.findByTestId('history-room-recent-old')

    await user.click(within(row).getByRole('button', { name: 'Actions for Recent old' }))
    await user.click(await screen.findByRole('menuitem', { name: /rename/i }))
    const input = within(row).getByRole('textbox', { name: 'Room name' })
    await user.clear(input)
    await user.type(input, 'Renamed room{Enter}')

    await waitFor(() => {
      expect(mocks.update).toHaveBeenCalledWith(
        'recent-old',
        { title: 'Renamed room' },
      )
    })

    await user.click(within(row).getByRole('button', { name: 'Actions for Renamed room' }))
    await user.click(await screen.findByRole('menuitem', { name: /^pin$/i }))
    expect(mocks.update).toHaveBeenCalledWith('recent-old', { is_pinned: true })
  })

  it('confirms deletion and navigates away when the active room is deleted', async () => {
    const user = userEvent.setup()
    renderHistory()
    const row = await screen.findByTestId('history-room-active-room')

    await user.click(within(row).getByRole('button', { name: 'Actions for Recent new' }))
    await user.click(await screen.findByRole('menuitem', { name: /delete/i }))
    expect(await screen.findByText('Delete conversation?')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Delete' }))

    await waitFor(() => expect(mocks.remove).toHaveBeenCalledWith('active-room'))
    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/chat'))
  })
})
