// Turn Trace store — Phase 1 decision-visibility surface.
//
// Consumes public `run_event` payload types over the existing SSE channel
// (llm_call_completed, llm_retry_scheduled, orchestrator_decision,
// tool_call_accepted, tool_call_completed) and folds them into a per-run
// decision → llm_call → tool_call tree for the Turn Trace panel.
//
// Phase 1 correlates nodes to turns via client_request_id (the existing
// channel); Phase 2 of the Room Stream Snapshot plan upgrades correlation
// to parent_event_id + room_seq ordering.

import { create } from 'zustand'
import { subscribeWithSelector } from 'zustand/middleware'

export const TRACE_RUN_EVENT_TYPES = [
  'llm_call_completed',
  'llm_retry_scheduled',
  'orchestrator_decision',
  'tool_call_accepted',
  'tool_call_completed',
] as const

export type TraceRunEventType = (typeof TRACE_RUN_EVENT_TYPES)[number]

export const TRACE_RUN_EVENT_TYPE_SET = new Set<string>(TRACE_RUN_EVENT_TYPES)

export function isTraceRunEventType(value: unknown): value is TraceRunEventType {
  return typeof value === 'string' && TRACE_RUN_EVENT_TYPE_SET.has(value)
}

export type TraceNodeKind = 'decision' | 'llm_call' | 'retry' | 'tool_call'

export interface TraceNode {
  /** Stable node id: run-scoped, unique per logical event. */
  id: string
  kind: TraceNodeKind
  runId: string
  clientRequestId: string | null
  receivedAt: number
  /** tool_call lifecycle: accepted | completed. */
  status?: string

  // llm_call fields
  model?: string
  provider?: string
  attempt?: number | null
  outcome?: string
  durationMs?: number | null
  usage?: { input: number | null; output: number | null }
  finishReason?: string

  // retry fields
  errorClass?: string
  retryDelayMs?: number | null

  // decision fields
  chosenAgents?: string[]
  planSteps?: Array<{ agent: string; summary: string }>
  reason?: string

  // tool_call fields
  toolName?: string
  argSummary?: unknown
  resultSummary?: string
  exitCode?: number | null
}

interface TraceStoreState {
  roomId: string | null
  /** node id → node */
  nodes: Record<string, TraceNode>
  /** run id → ordered node ids (append order within the run) */
  runOrder: Record<string, string[]>
  version: number

  applyRunEvent: (payload: {
    eventId: string
    runId: string
    type: string
    payload: Record<string, unknown>
    correlationId: string | null
  }) => void
  setRoom: (roomId: string) => void
  clearRoom: () => void
}

const INITIAL_STATE = {
  roomId: null as string | null,
  nodes: {} as Record<string, TraceNode>,
  runOrder: {} as Record<string, string[]>,
  version: 0,
}

function asString(value: unknown): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value : undefined
}

function asInt(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? Math.trunc(value) : null
}

function asUsage(value: unknown): { input: number | null; output: number | null } | undefined {
  if (!value || typeof value !== 'object') return undefined
  const record = value as Record<string, unknown>
  const input = asInt(record.input)
  const output = asInt(record.output)
  if (input === null && output === null) return undefined
  return { input, output }
}

function asPlanSteps(value: unknown): Array<{ agent: string; summary: string }> | undefined {
  if (!Array.isArray(value)) return undefined
  const steps: Array<{ agent: string; summary: string }> = []
  for (const step of value) {
    if (!step || typeof step !== 'object') continue
    const record = step as Record<string, unknown>
    const agent = asString(record.agent)
    if (!agent) continue
    steps.push({ agent, summary: typeof record.summary === 'string' ? record.summary : '' })
  }
  return steps.length > 0 ? steps : undefined
}

function asStringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined
  const items = value.filter((item): item is string => typeof item === 'string')
  return items.length > 0 ? items : undefined
}

function nodeFromRunEvent(payload: {
  eventId: string
  runId: string
  type: string
  payload: Record<string, unknown>
  correlationId: string | null
}): TraceNode | null {
  const data = payload.payload
  const base = {
    id: `${payload.runId}:${payload.type}:${payload.eventId}`,
    runId: payload.runId,
    clientRequestId: payload.correlationId,
    receivedAt: Date.now(),
  }

  switch (payload.type) {
    case 'llm_call_completed':
      return {
        ...base,
        kind: 'llm_call',
        model: asString(data.model),
        provider: asString(data.provider),
        attempt: asInt(data.attempt),
        outcome: asString(data.outcome),
        durationMs: asInt(data.duration_ms),
        usage: asUsage(data.usage),
        finishReason: asString(data.finish_reason),
      }
    case 'llm_retry_scheduled':
      return {
        ...base,
        kind: 'retry',
        attempt: asInt(data.attempt),
        errorClass: asString(data.error_class),
        retryDelayMs: asInt(data.retry_delay_ms),
      }
    case 'orchestrator_decision':
      return {
        ...base,
        kind: 'decision',
        chosenAgents: asStringArray(data.chosen_agents),
        planSteps: asPlanSteps(data.plan_steps),
        reason: typeof data.reason === 'string' ? data.reason : undefined,
      }
    case 'tool_call_accepted':
      return {
        ...base,
        // Phase 1 merges accepted/completed pairs by tool name within the run;
        // Phase 2 replaces this with parent_event_id correlation.
        id: `${payload.runId}:tool_call:${asString(data.tool_name) ?? 'unknown'}`,
        kind: 'tool_call',
        status: 'accepted',
        toolName: asString(data.tool_name),
        argSummary: data.arg_summary ?? undefined,
      }
    case 'tool_call_completed':
      return {
        ...base,
        id: `${payload.runId}:tool_call:${asString(data.tool_name) ?? 'unknown'}`,
        kind: 'tool_call',
        status: 'completed',
        toolName: asString(data.tool_name),
        resultSummary: typeof data.result_summary === 'string' ? data.result_summary : undefined,
        exitCode: asInt(data.exit_code),
        durationMs: asInt(data.duration_ms),
      }
    default:
      return null
  }
}

export const useTraceStore = create<TraceStoreState>()(
  subscribeWithSelector((set, get) => ({
    ...INITIAL_STATE,

    applyRunEvent: (payload) => {
      if (!isTraceRunEventType(payload.type)) return
      const node = nodeFromRunEvent(payload)
      if (!node) return

      const current = get()
      // Merge accepted/completed tool-call pairs into one node.
      const existing = current.nodes[node.id]
      const merged: TraceNode = existing
        ? {
            ...existing,
            ...node,
            // A completed tool call supersedes its accepted counterpart but
            // keeps the argument summary the accepted event carried.
            argSummary: node.argSummary ?? existing.argSummary,
            receivedAt: node.status === 'completed' ? node.receivedAt : existing.receivedAt,
          }
        : node

      set((state) => {
        const runOrder = state.runOrder[node.runId] ?? []
        const order = runOrder.includes(node.id) ? runOrder : [...runOrder, node.id]
        return {
          nodes: { ...state.nodes, [node.id]: merged },
          runOrder: { ...state.runOrder, [node.runId]: order },
          version: state.version + 1,
        }
      })
    },

    setRoom: (roomId) => set({ ...INITIAL_STATE, roomId }),

    clearRoom: () => set({ ...INITIAL_STATE }),
  }))
)

/** Selector: trace nodes for one client request, in receive order. */
export function selectTraceNodesForClientRequest(
  state: Pick<TraceStoreState, 'nodes'>,
  clientRequestId: string | null | undefined,
): TraceNode[] {
  if (!clientRequestId) return []
  const nodes = Object.values(state.nodes).filter(
    (node) => node.clientRequestId === clientRequestId,
  )
  nodes.sort((a, b) => a.receivedAt - b.receivedAt)
  return nodes
}

/** Selector: trace nodes for one run, in receive order. */
export function selectTraceNodesForRun(
  state: Pick<TraceStoreState, 'nodes' | 'runOrder'>,
  runId: string | null | undefined,
): TraceNode[] {
  if (!runId) return []
  const order = state.runOrder[runId] ?? []
  return order
    .map((id) => state.nodes[id])
    .filter((node): node is TraceNode => Boolean(node))
}
