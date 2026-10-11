export interface GraphDisplayPrefs { alwaysSubnetLabels: boolean; alwaysAgentLabels: boolean }
export const DEFAULT_GRAPH_DISPLAY: GraphDisplayPrefs = { alwaysSubnetLabels: true, alwaysAgentLabels: false }

type Neighbors = ReadonlyMap<string, ReadonlySet<string>>

export function buildNeighbors(memberships: readonly { networkId: string; agentId: string }[]): Map<string, Set<string>> {
  const neighbors = new Map<string, Set<string>>()
  const link = (from: string, to: string) => {
    let set = neighbors.get(from)
    if (!set) neighbors.set(from, (set = new Set()))
    set.add(to)
  }
  for (const { networkId, agentId } of memberships) {
    link(`network:${networkId}`, `agent:${agentId}`)
    link(`agent:${agentId}`, `network:${networkId}`)
  }
  return neighbors
}

export function isLabelVisible(key: string, ctx: {
  hovered: string | null
  selected: string | null
  prefs: GraphDisplayPrefs
  neighbors: Neighbors
}): boolean {
  if (key === ctx.hovered || key === ctx.selected) return true
  if (key.startsWith('network:')) return ctx.prefs.alwaysSubnetLabels
  if (ctx.prefs.alwaysAgentLabels) return true
  // An agent's neighbors are subnets, so only a hovered or selected subnet reveals member names.
  return [ctx.hovered, ctx.selected].some(anchor => anchor !== null && Boolean(ctx.neighbors.get(anchor)?.has(key)))
}

export interface Emphasis { nodes: Set<string>; anchors: Set<string> }

export function emphasisFor(ctx: {
  hovered: string | null
  selected: string | null
  search: ReadonlySet<string> | null
  neighbors: Neighbors
}): Emphasis | null {
  const anchors = ctx.hovered ? [ctx.hovered] : ctx.search ? [...ctx.search] : ctx.selected ? [ctx.selected] : null
  if (!anchors) return null
  const nodes = new Set<string>()
  for (const anchor of anchors) {
    nodes.add(anchor)
    for (const neighbor of ctx.neighbors.get(anchor) ?? []) nodes.add(neighbor)
  }
  return { nodes, anchors: new Set(anchors) }
}
