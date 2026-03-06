import { describe, it, expect, vi, afterEach } from 'vitest'
import { render, screen, fireEvent, cleanup } from '@testing-library/react'
import { WorkflowMessage, WORKFLOW_STAGE } from '@/components/workflow-message'
import type { MetaTask, Agent, AgentCard } from '@/lib/types'

function makeMetaTask(overrides: Partial<MetaTask> = {}): MetaTask {
  return {
    task_id: 'mt-1',
    parent_task_id: 'base-1',
    task_description: 'Analyze dataset',
    execution_order: 1,
    ...overrides,
  }
}

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    agent_id: 'agent-1',
    agent_card: {
      name: 'Data Agent',
      description: 'Handles data analysis',
      url: 'https://example.com/agent',
      version: '1.0',
    } as AgentCard,
    ...overrides,
  }
}

const noop = () => {}

describe('WorkflowMessage', () => {
  afterEach(() => {
    cleanup()
  })

  it('renders DECOMPOSED stage with meta-task list', () => {
    const metaTasks = [
      makeMetaTask({ task_id: 'mt-1', task_description: 'Fetch data' }),
      makeMetaTask({ task_id: 'mt-2', task_description: 'Process results' }),
    ]
    render(
      <WorkflowMessage
        baseTaskId="base-1"
        metaTasks={metaTasks}
        stage={WORKFLOW_STAGE.DECOMPOSED}
        onNext={noop}
        onRetry={noop}
      />
    )

    expect(screen.getByText('Task Decomposition Complete')).toBeTruthy()
    expect(screen.getByText('Fetch data')).toBeTruthy()
    expect(screen.getByText('Process results')).toBeTruthy()
    expect(screen.getByText('2 tasks')).toBeTruthy()
  })

  it('renders AGENTS_ASSIGNED stage showing agent names', () => {
    const agents = [makeAgent({ agent_id: 'agent-1' })]
    const metaTasks = [
      makeMetaTask({ task_id: 'mt-1', agent_id: 'agent-1', task_description: 'Analyze' }),
    ]
    render(
      <WorkflowMessage
        baseTaskId="base-1"
        metaTasks={metaTasks}
        agents={agents}
        stage={WORKFLOW_STAGE.AGENTS_ASSIGNED}
        onNext={noop}
        onRetry={noop}
      />
    )

    expect(screen.getByText('Agents Assigned')).toBeTruthy()
    expect(screen.getByText('Data Agent')).toBeTruthy()
  })

  it('renders RUNNING stage with progress indicators', () => {
    const metaTasks = [makeMetaTask()]
    const { container } = render(
      <WorkflowMessage
        baseTaskId="base-1"
        metaTasks={metaTasks}
        stage={WORKFLOW_STAGE.RUNNING}
        onNext={noop}
        onRetry={noop}
      />
    )

    expect(screen.getByText('Workflow Running')).toBeTruthy()
    const spinner = container.querySelector('.animate-spin')
    expect(spinner).toBeTruthy()
  })

  it('renders COMPLETED stage', () => {
    const metaTasks = [makeMetaTask()]
    render(
      <WorkflowMessage
        baseTaskId="base-1"
        metaTasks={metaTasks}
        stage={WORKFLOW_STAGE.COMPLETED}
        onNext={noop}
        onRetry={noop}
      />
    )

    expect(screen.getByText('Workflow Completed')).toBeTruthy()
  })

  it('retry button calls onRetry handler', () => {
    const onRetry = vi.fn()
    const metaTasks = [makeMetaTask()]
    render(
      <WorkflowMessage
        baseTaskId="base-1"
        metaTasks={metaTasks}
        stage={WORKFLOW_STAGE.DECOMPOSED}
        onNext={noop}
        onRetry={onRetry}
      />
    )

    const retryButton = screen.getByRole('button', { name: /retry/i })
    fireEvent.click(retryButton)
    expect(onRetry).toHaveBeenCalledTimes(1)
  })
})
