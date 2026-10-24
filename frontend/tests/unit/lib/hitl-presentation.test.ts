import { describe, expect, it } from 'vitest'
import { parseHitlPresentation, presentationEqual } from '@/lib/hitl/presentation'
import { buildPendingHitlIncomingMessage } from '@/lib/hitl/hitl-message-projection'
import { selectPendingHitls } from '@/lib/selectors/select-hitl'

describe('parseHitlPresentation', () => {
  it('reads header, optional flag, Other, and described choices from the wire', () => {
    expect(parseHitlPresentation({
      header: 'Destination',
      required: false,
      allow_other: true,
      choice_options: [
        { value: 'Kyoto', description: 'Temples and food' },
        { value: 'Osaka', label: 'Osaka city' },
      ],
    })).toEqual({
      header: 'Destination',
      required: false,
      allowOther: true,
      choiceOptions: [
        { value: 'Kyoto', description: 'Temples and food' },
        { value: 'Osaka', label: 'Osaka city' },
      ],
    })
  })

  it('defaults to a required question without Other', () => {
    expect(parseHitlPresentation({})).toEqual({ required: true, allowOther: false })
  })

  it('ignores malformed fields from untrusted payloads', () => {
    expect(parseHitlPresentation({
      header: 42,
      required: 'no',
      allow_other: 'yes',
      choice_options: [{ value: 7 }, 'Kyoto', { value: 'Osaka', description: null }],
    })).toEqual({ required: true, allowOther: false, choiceOptions: [{ value: 'Osaka' }] })
  })

  it('compares presentations by value', () => {
    expect(presentationEqual(parseHitlPresentation({ header: 'A' }), parseHitlPresentation({ header: 'A' }))).toBe(true)
    expect(presentationEqual(parseHitlPresentation({ header: 'A' }), parseHitlPresentation({ header: 'B' }))).toBe(false)
    expect(presentationEqual(undefined, undefined)).toBe(true)
  })
})

describe('presentation in the chat store path', () => {
  it('flows from the projection to the pending question', () => {
    const presentation = parseHitlPresentation({ header: 'Destination', allow_other: true })
    const incoming = buildPendingHitlIncomingMessage({
      conversationId: 'room-1',
      messageId: 'agent-msg-1',
      requestId: 'destination',
      source: 'agent',
      prompt: 'Where?',
      promptType: 'single_choice',
      choices: ['Kyoto', 'Osaka'],
      presentation,
      timestamp: '2026-10-15T00:00:00.000Z',
      agentId: 'agent-1',
      agentName: 'Travel Planner Agent',
      agentSource: 'local',
      expiresAt: null,
      interactionId: 'trip-1',
      groupId: 'trip-1',
      groupTotal: 1,
      groupIndex: 0,
      stepNumber: null,
      totalSteps: null,
      relatedMessageId: 'user-1',
      clientRequestId: 'cr-1',
    })
    expect(incoming.hitlPresentation).toEqual(presentation)

    const entity = { ...incoming, status: 'complete' } as never
    const [pending] = selectPendingHitls('room-1', { [incoming.id]: entity }, [incoming.id])
    expect(pending.presentation).toEqual(presentation)
  })
})
