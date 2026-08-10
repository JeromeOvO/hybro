import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Youtube } from 'lucide-react'
import {
  createAgentGroup,
  getAgentGroup,
  listAgentGroups,
  updateAgentGroup,
} from '@/lib/api/agent-group'
import type { Agent } from '@/lib/types/agent'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { UseCaseTemplate } from '@/lib/use-case-templates'
import {
  ensureUseCaseTeam,
  findUseCaseTeam,
  getUseCaseTeamDescription,
} from '@/lib/use-case-team'

vi.mock('@/lib/api/agent-group', () => ({
  createAgentGroup: vi.fn(),
  getAgentGroup: vi.fn(),
  listAgentGroups: vi.fn(),
  updateAgentGroup: vi.fn(),
}))

const mockCreateAgentGroup = vi.mocked(createAgentGroup)
const mockGetAgentGroup = vi.mocked(getAgentGroup)
const mockListAgentGroups = vi.mocked(listAgentGroups)
const mockUpdateAgentGroup = vi.mocked(updateAgentGroup)

function makeAgent(id: string, name: string): Agent {
  return {
    agent_id: id,
    agent_card: {
      name,
      description: '',
      url: `https://example.com/${id}`,
      version: '1.0.0',
      provider: { organization: 'test', url: 'https://test.com' },
      capabilities: {},
      protocolVersion: '1.0.0',
      skills: [],
      defaultInputModes: ['text'],
      defaultOutputModes: ['text'],
    },
  }
}

const catalog = [
  makeAgent('agent-001', 'Agent One'),
  makeAgent('agent-002', 'Agent Two'),
]

const template: UseCaseTemplate = {
  id: 'test-template',
  icon: Youtube,
  title: 'Test Template',
  description: 'A test template',
  agents: [
    { agentId: 'agent-001', agentName: 'Agent One' },
    { agentId: 'agent-002', agentName: 'Agent Two' },
  ],
  prefillMessage: 'Start from this prompt',
  tag: null,
}

function makePresetTeam(): AgentGroup {
  return {
    group_id: 'preset-team-1',
    name: 'Test Template Team',
    description: getUseCaseTeamDescription(template),
    type: 'user',
    owner_id: 'user-1',
    agents: ['agent-001', 'agent-002'],
  }
}

describe('use case preset teams', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('finds a preset by its stable marker rather than its display name', () => {
    const preset = makePresetTeam()
    const sameName: AgentGroup = {
      ...preset,
      group_id: 'user-team',
      description: 'Created manually',
    }

    expect(findUseCaseTeam([sameName, preset], template)).toBe(preset)
  })

  it('reuses an existing team without creating a duplicate', async () => {
    const preset = makePresetTeam()
    mockListAgentGroups.mockResolvedValue({ success: true, groups: [preset] })

    const result = await ensureUseCaseTeam({
      template,
      ownerId: 'user-1',
      catalog,
    })

    expect(result).toBe(preset)
    expect(mockCreateAgentGroup).not.toHaveBeenCalled()
  })

  it('restores the template agents when an existing preset was edited', async () => {
    const stalePreset = { ...makePresetTeam(), agents: ['agent-001'] }
    const repairedPreset = makePresetTeam()
    mockListAgentGroups.mockResolvedValue({ success: true, groups: [stalePreset] })
    mockUpdateAgentGroup.mockResolvedValue({
      success: true,
      group: repairedPreset,
    })

    await expect(ensureUseCaseTeam({
      template,
      ownerId: 'user-1',
      catalog,
    })).resolves.toBe(repairedPreset)
    expect(mockUpdateAgentGroup).toHaveBeenCalledWith({
      group_id: 'preset-team-1',
      agents: ['agent-001', 'agent-002'],
    }, undefined)
    expect(mockCreateAgentGroup).not.toHaveBeenCalled()
  })

  it('accepts a concurrent no-op update when persisted agents are correct', async () => {
    const stalePreset = { ...makePresetTeam(), agents: ['agent-001'] }
    const reconciledPreset = makePresetTeam()
    mockListAgentGroups.mockResolvedValue({ success: true, groups: [stalePreset] })
    mockUpdateAgentGroup.mockResolvedValue({
      success: false,
      error: 'Failed to update agent group',
    })
    mockGetAgentGroup.mockResolvedValue({
      success: true,
      group: reconciledPreset,
    })

    await expect(ensureUseCaseTeam({
      template,
      ownerId: 'user-1',
      catalog,
    })).resolves.toBe(reconciledPreset)
    expect(mockGetAgentGroup).toHaveBeenCalledWith('preset-team-1', undefined)
  })

  it('keeps the update failure when persisted agents are still stale', async () => {
    const stalePreset = { ...makePresetTeam(), agents: ['agent-001'] }
    mockListAgentGroups.mockResolvedValue({ success: true, groups: [stalePreset] })
    mockUpdateAgentGroup.mockResolvedValue({
      success: false,
      error: 'Failed to update agent group',
    })
    mockGetAgentGroup.mockResolvedValue({
      success: true,
      group: stalePreset,
    })

    await expect(ensureUseCaseTeam({
      template,
      ownerId: 'user-1',
      catalog,
    })).rejects.toThrow('Failed to update agent group')
  })

  it('creates the preset team with the resolved template agents when absent', async () => {
    const preset = makePresetTeam()
    mockListAgentGroups.mockResolvedValue({ success: true, groups: [] })
    mockCreateAgentGroup.mockResolvedValue({ success: true, group: preset })

    const result = await ensureUseCaseTeam({
      template,
      ownerId: 'user-1',
      catalog,
    })

    expect(result).toBe(preset)
    expect(mockCreateAgentGroup).toHaveBeenCalledWith({
      name: 'Test Template Team',
      description: getUseCaseTeamDescription(template),
      owner_id: 'user-1',
      agents: ['agent-001', 'agent-002'],
      preset_key: 'hybro-use-case:test-template',
    }, undefined)
  })

  it('does not call the API when a template agent is unavailable', async () => {
    await expect(ensureUseCaseTeam({
      template,
      ownerId: 'user-1',
      catalog: [],
    })).rejects.toThrow('Some agents in this template are unavailable')

    expect(mockListAgentGroups).not.toHaveBeenCalled()
    expect(mockCreateAgentGroup).not.toHaveBeenCalled()
  })

  it('reuses a team created concurrently after a failed create', async () => {
    const preset = makePresetTeam()
    mockListAgentGroups
      .mockResolvedValueOnce({ success: true, groups: [] })
      .mockResolvedValueOnce({ success: true, groups: [preset] })
    mockCreateAgentGroup.mockResolvedValue({ success: false, error: 'Conflict' })

    await expect(ensureUseCaseTeam({
      template,
      ownerId: 'user-1',
      catalog,
    })).resolves.toBe(preset)
  })
})
