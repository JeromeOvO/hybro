import { afterEach, describe, expect, it } from 'vitest'
import { cleanup, fireEvent, render, screen } from '../../../utils/test-utils'
import { TurnTracePanel } from '@/components/conversation/TurnTracePanel'
import type { TraceNode } from '@/stores/trace-store'

afterEach(cleanup)

describe('TurnTracePanel', () => {
it('renders accepted-only tool history as neutral when its Turn is terminal', () => {
  const node: TraceNode = {
    id: 'run-1:tool:call-1',
    kind: 'tool_call',
    runId: 'run-1',
    clientRequestId: 'request-1',
    receivedAt: 1,
    status: 'accepted',
    callId: 'call-1',
    toolName: 'Weather Agent',
  }
  render(
    <TurnTracePanel
      nodes={[node]}
      statusEntries={[]}
      isRunning={false}
      turnTerminal
    />,
  )

  fireEvent.click(screen.getByRole('button', { name: /finished, 0\.0s/i }))
  expect(screen.getByText('Called Weather Agent')).toBeInTheDocument()
  expect(screen.getByText('Outcome unavailable')).toBeInTheDocument()
  expect(screen.queryByText('Running')).not.toBeInTheDocument()
  expect(screen.queryByText('Input')).not.toBeInTheDocument()
  expect(screen.queryByText('Output')).not.toBeInTheDocument()
  expect(screen.queryByRole('log')).not.toBeInTheDocument()
})

it('uses every durable node timestamp for terminal Turn duration without rendering hidden LLM metadata', () => {
  const startedAt = '1970-01-01T00:00:00.000Z'
  const nodes: TraceNode[] = [
    {
      id: 'run-1:tool:call-1', kind: 'tool_call', runId: 'run-1',
      clientRequestId: 'request-1', receivedAt: 1000, status: 'completed',
      callId: 'call-1', toolName: 'Weather Agent', exitCode: 0,
    },
    {
      id: 'run-1:llm:2', kind: 'llm_call', runId: 'run-1',
      clientRequestId: 'request-1', receivedAt: 5000, model: 'private-model',
    },
  ]

  const { container } = render(
    <TurnTracePanel
      nodes={nodes}
      statusEntries={[]}
      startedAt={startedAt}
      turnTerminal
    />,
  )

  expect(screen.getByRole('button', { name: 'Finished, 5.0s' })).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: 'Finished, 5.0s' }))
  expect(screen.queryByText('private-model')).not.toBeInTheDocument()
  const tool = container.querySelector('[data-kind="tool_call"]')
  expect(tool).toHaveClass('conversation-trace-action-success')
  expect(tool?.querySelector('[data-slot="marker-icon"] svg')).toHaveClass('lucide-bot')
})
})
