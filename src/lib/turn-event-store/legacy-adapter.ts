import type { TurnEvent } from '@/stores/turn-event-store/types'
import type { RoomMessage } from '@/lib/types/response'

export interface TurnPseudoEvents {
  turnId: string
  events: TurnEvent[]
}

let seqCounter = 0
function nextSeq(): number { return ++seqCounter }
function makeEventId(): string { return `legacy_${Date.now()}_${Math.random().toString(36).slice(2, 8)}` }

export function convertLegacyMessagesToTurnEvents(
  apiMessages: RoomMessage[],
): TurnPseudoEvents[] {
  seqCounter = 0

  const userMessages = apiMessages.filter(m => m.message_type === 'user')
  const agentMessages = apiMessages.filter(m => m.message_type === 'agent')

  const agentsByTurn = new Map<string, RoomMessage[]>()
  for (const msg of agentMessages) {
    const turnId = msg.related_message_id
    if (!turnId) continue
    const existing = agentsByTurn.get(turnId) ?? []
    existing.push(msg)
    agentsByTurn.set(turnId, existing)
  }

  const result: TurnPseudoEvents[] = []

  for (const userMsg of userMessages) {
    const turnId = userMsg.message_id
    const turnAgents = agentsByTurn.get(turnId) ?? []
    const ts = userMsg.message_created_at ? new Date(userMsg.message_created_at).getTime() : Date.now()

    const events: TurnEvent[] = []

    // turn_started
    events.push({
      eventId: makeEventId(),
      turnId,
      seq: nextSeq(),
      ts,
      type: 'turn_started',
      userInput: {
        text: userMsg.message_content?.message_text ?? '',
        attachments: [],
      },
    } as TurnEvent)

    // For each agent: slot_opened + slot_snapshot + slot_terminated
    for (const agentMsg of turnAgents) {
      const slotTs = agentMsg.message_created_at ? new Date(agentMsg.message_created_at).getTime() : ts

      events.push({
        eventId: makeEventId(),
        turnId,
        seq: nextSeq(),
        ts: slotTs,
        type: 'slot_opened',
        slotId: agentMsg.message_id,
        slotType: 'agent',
        agentId: agentMsg.agent_id ?? '',
        agentName: undefined, // RoomMessage doesn't include agent_name, will be resolved later
      } as TurnEvent)

      events.push({
        eventId: makeEventId(),
        turnId,
        seq: nextSeq(),
        ts: slotTs,
        type: 'slot_snapshot',
        slotId: agentMsg.message_id,
        content: agentMsg.message_content?.message_text ?? '',
        artifacts: [],
      } as TurnEvent)

      events.push({
        eventId: makeEventId(),
        turnId,
        seq: nextSeq(),
        ts: slotTs,
        type: 'slot_terminated',
        slotId: agentMsg.message_id,
        status: 'completed',
      } as TurnEvent)
    }

    // turn_completed
    const lastTs = turnAgents.length > 0 && turnAgents[turnAgents.length - 1].message_created_at
      ? new Date(turnAgents[turnAgents.length - 1].message_created_at!).getTime()
      : ts
    events.push({
      eventId: makeEventId(),
      turnId,
      seq: nextSeq(),
      ts: lastTs,
      type: 'turn_completed',
      durationMs: lastTs - ts,
    } as TurnEvent)

    result.push({ turnId, events })
  }

  return result
}
