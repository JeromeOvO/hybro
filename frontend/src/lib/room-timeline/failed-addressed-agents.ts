import type { TurnProjection } from '@/lib/turn-lifecycle/types'

export interface FailedAddressedAgent {
  name: string
  reason?: string
}

const MENTION_TOKEN = /<@[^|>]+\|([^>]+)>/g

/** Agent names the user addressed with mention chips, in order. */
export function addressedAgentNames(userText: string): string[] {
  const names = [...userText.matchAll(MENTION_TOKEN)].map((match) => match[1].trim())
  return [...new Set(names.filter(Boolean))]
}

/**
 * Agents the user addressed whose every call in this Turn failed.
 *
 * Any answer such a Turn produces came from Hybro rather than the addressed
 * Agent, so the UI must say so regardless of what the model wrote.
 */
export function failedAddressedAgents(
  turn: TurnProjection,
  userText: string,
): FailedAddressedAgent[] {
  return addressedAgentNames(userText).flatMap((name) => {
    const calls = turn.activity.filter((item) => (
      item.kind === 'tool' && item.executionKind === 'agent' && item.targetName === name
    ))
    if (calls.length === 0 || calls.some((item) => item.kind === 'tool' && item.status !== 'failed')) {
      return []
    }
    const last = calls.at(-1)
    const reason = last?.kind === 'tool' ? (last.errorMessage ?? last.errorCode) : undefined
    return [{ name, reason }]
  })
}
