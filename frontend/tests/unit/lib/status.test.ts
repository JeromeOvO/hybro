import { describe, expect, it } from 'vitest'
import { agentStatusRank, agentStatusView, gatewayStatusView, LOAD_ERROR_STATUS, roomStatusView } from '@/lib/status'

describe('status mapping (spec 7.2)', () => {
  it('maps agent states', () => {
    expect(agentStatusView('active')).toEqual({ label: 'Online', tone: 'online', dot: 'filled', colorLabel: false })
    expect(agentStatusView('inactive')).toMatchObject({ label: 'Offline', tone: 'offline', dot: 'hollow' })
    expect(agentStatusView('unavailable')).toMatchObject({ label: 'Offline', tone: 'offline', dot: 'hollow' })
    expect(agentStatusView('unknown')).toMatchObject({ label: 'Unverified', tone: 'unverified', dot: 'hollow' })
    expect(agentStatusView('deleted')).toMatchObject({ label: 'Removed', tone: 'error', dot: 'hollow' })
  })

  it('never maps offline to the error tone', () => {
    expect(agentStatusView('inactive').tone).not.toBe('error')
    expect(gatewayStatusView('unavailable').tone).not.toBe('error')
  })

  it('maps gateway states', () => {
    expect(gatewayStatusView('ready')).toMatchObject({ label: 'Ready', tone: 'online', dot: 'filled' })
    expect(gatewayStatusView('unsupported_auth')).toMatchObject({ label: 'Unavailable', tone: 'offline' })
    expect(gatewayStatusView('error')).toMatchObject({ label: "Can't check", tone: 'error', dot: 'filled' })
    expect(gatewayStatusView('checking')).toMatchObject({ label: 'Checking', dot: 'spinner' })
  })

  it('maps room states, with only Needs input coloring its label', () => {
    expect(roomStatusView('idle')).toBeNull()
    expect(roomStatusView('queued')).toMatchObject({ label: 'Queued', tone: 'offline', colorLabel: false })
    expect(roomStatusView('processing')).toMatchObject({ label: 'Running', tone: 'offline', dot: 'spinner' })
    expect(roomStatusView('awaiting_input')).toEqual({ label: 'Needs input', tone: 'warning', dot: 'filled', colorLabel: true })
  })

  it('exposes the load error view and a status sort rank', () => {
    expect(LOAD_ERROR_STATUS).toMatchObject({ label: "Couldn't load", tone: 'error' })
    expect(agentStatusRank('active')).toBeLessThan(agentStatusRank('unknown'))
    expect(agentStatusRank('unknown')).toBeLessThan(agentStatusRank('inactive'))
    expect(agentStatusRank('inactive')).toBeLessThan(agentStatusRank('deleted'))
  })
})
