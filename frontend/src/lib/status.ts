import type { McpStatus } from '@/lib/mcp'
import type { RoomHistoryStatus } from '@/lib/api/room'
import type { NetworkAgent } from '@/components/networks/types'

export type StatusTone = 'online' | 'offline' | 'unverified' | 'warning' | 'error'
export type StatusDot = 'filled' | 'hollow' | 'spinner'

export interface StatusView {
  label: string
  tone: StatusTone
  dot: StatusDot
  colorLabel: boolean
}

const view = (label: string, tone: StatusTone, dot: StatusDot, colorLabel = false): StatusView =>
  ({ label, tone, dot, colorLabel })

export function agentStatusView(status: NetworkAgent['status']): StatusView {
  switch (status) {
    case 'active': return view('Online', 'online', 'filled')
    case 'inactive':
    case 'unavailable': return view('Offline', 'offline', 'hollow')
    case 'deleted': return view('Removed', 'error', 'hollow')
    default: return view('Unverified', 'unverified', 'hollow')
  }
}

export function agentStatusRank(status: NetworkAgent['status']): number {
  return { active: 0, unknown: 1, inactive: 2, unavailable: 2, deleted: 3 }[status]
}

export function gatewayStatusView(status: McpStatus | 'checking' | 'error'): StatusView {
  switch (status) {
    case 'ready': return view('Ready', 'online', 'filled')
    case 'checking': return view('Checking', 'offline', 'spinner')
    case 'error': return view("Can't check", 'error', 'filled')
    default: return view('Unavailable', 'offline', 'hollow')
  }
}

export function roomStatusView(status: RoomHistoryStatus): StatusView | null {
  switch (status) {
    case 'queued': return view('Queued', 'offline', 'filled')
    case 'processing': return view('Running', 'offline', 'spinner')
    case 'awaiting_input': return view('Needs input', 'warning', 'filled', true)
    default: return null
  }
}

export const LOAD_ERROR_STATUS: StatusView = view("Couldn't load", 'error', 'filled')
