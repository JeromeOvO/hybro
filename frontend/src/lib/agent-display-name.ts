const GENERIC_AGENT_NAMES = new Set([
  'agent',
  'unknown',
  'unknown agent',
])

const OPAQUE_PREFIXES = ['agent_', 'binding-', 'inv_', 'orchestrator:']

/** Return a safe, human-readable public Agent name or undefined. */
export function specificPublicAgentName(value: unknown): string | undefined {
  if (typeof value !== 'string') return undefined
  const name = value.trim()
  if (!name || GENERIC_AGENT_NAMES.has(name.toLocaleLowerCase())) return undefined
  if (OPAQUE_PREFIXES.some((prefix) => name.startsWith(prefix))) return undefined
  return name.slice(0, 160)
}

/** Once known, an exact public Agent identity is authoritative. Updates only
 * fill a missing/generic name; activity or skill labels must not replace it. */
export function patchedPublicAgentName(
  current: unknown,
  incoming: unknown,
): string | undefined {
  return specificPublicAgentName(current) ?? specificPublicAgentName(incoming)
}
