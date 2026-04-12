import type { TurnEvent, ArtifactData } from '@/stores/turn-event-store/types'
import type { ArtifactPart } from '@/stores/message-store/types'
import type { AttachmentData } from '@/lib/types/attachments'
import type { RoomMessage } from '@/lib/types/response'

export interface TurnPseudoEvents {
  turnId: string
  events: TurnEvent[]
}

function makeEventId(): string { return `legacy_${Date.now()}_${Math.random().toString(36).slice(2, 8)}` }

/** Extract user-facing attachments from a message's content. */
function extractAttachments(msg: RoomMessage): AttachmentData[] {
  const raw = msg.message_content?.attachments
  if (!Array.isArray(raw) || raw.length === 0) return []
  return raw
    .filter((att: Record<string, unknown>) => typeof att.file_id === 'string' && typeof att.mime_type === 'string')
    .map((att: Record<string, unknown>) => ({
      fileId: att.file_id as string,
      fileUrl: (att.file_url as string) || undefined,
      mimeType: att.mime_type as string,
      fileName: (att.file_name as string) || 'unknown',
      sizeBytes: (att.size_bytes as number) || 0,
    }))
}

/** Extract multimodal artifacts from an agent message's task. */
function extractArtifacts(msg: RoomMessage): ArtifactData[] {
  const messageTask = msg.message_content?.message_task
  const rawArtifacts = (messageTask as Record<string, unknown> | null | undefined)?.artifacts as Record<string, unknown>[] | undefined
  if (!Array.isArray(rawArtifacts) || rawArtifacts.length === 0) return []

  return rawArtifacts
    .map((a) => {
      const rawParts = a.parts as Record<string, unknown>[] | undefined
      if (!Array.isArray(rawParts) || rawParts.length === 0) return null

      const parts = rawParts
        .map((p) => {
          const root = (p.root ?? p) as Record<string, unknown>
          const kind = (root.kind as string) || 'text'
          if (kind === 'text') return null
          const fileData = root.file as Record<string, unknown> | undefined
          return {
            kind: kind as ArtifactPart['kind'],
            text: root.text as string | undefined,
            file: fileData ? {
              uri: fileData.uri as string | undefined,
              bytes: fileData.bytes as string | undefined,
              mime_type: (fileData.mime_type || fileData.mimeType) as string | undefined,
              name: fileData.name as string | undefined,
            } : undefined,
            data: root.data as Record<string, unknown> | undefined,
          }
        })
        .filter((p): p is NonNullable<typeof p> => p !== null) as ArtifactPart[]

      if (parts.length === 0) return null
      return {
        artifactId: (a.artifactId || a.artifact_id || `legacy-${msg.message_id}`) as string,
        name: a.name as string | undefined,
        parts,
      }
    })
    .filter((a): a is NonNullable<typeof a> => a !== null) as ArtifactData[]
}

export function convertLegacyMessagesToTurnEvents(
  apiMessages: RoomMessage[],
): TurnPseudoEvents[] {
  let seqCounter = 0
  function nextSeq(): number { return ++seqCounter }

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
        attachments: extractAttachments(userMsg),
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
        artifacts: extractArtifacts(agentMsg),
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
