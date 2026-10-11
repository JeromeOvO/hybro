export type NetworkView = 'topology' | 'structure'
export type StructureLayout = 'list' | 'subnet'
export type NetworkScope = { kind: 'all' } | { kind: 'unassigned' } | { kind: 'subnet'; id: string }
export type NetworkSelect = { type: 'agent' | 'subnet'; id: string } | null

export interface NetworkUrlState {
  view: NetworkView
  q: string
  scope: NetworkScope
  layout: StructureLayout
  select: NetworkSelect
}

export const DEFAULT_NETWORK_URL_STATE: NetworkUrlState = {
  view: 'topology',
  q: '',
  scope: { kind: 'all' },
  layout: 'list',
  select: null,
}

function prefixed(value: string | null, prefix: string): string | null {
  if (!value?.startsWith(prefix)) return null
  const id = value.slice(prefix.length)
  return id ? id : null
}

export function parseNetworkUrlState(params: URLSearchParams): NetworkUrlState {
  const view = params.get('view') === 'structure' ? 'structure' : 'topology'
  const layout = params.get('layout') === 'subnet' ? 'subnet' : 'list'
  const rawScope = params.get('scope')
  const subnetScope = prefixed(rawScope, 'subnet:')
  const scope: NetworkScope = rawScope === 'unassigned'
    ? { kind: 'unassigned' }
    : subnetScope ? { kind: 'subnet', id: subnetScope } : { kind: 'all' }
  const rawSelect = params.get('select')
  const agent = prefixed(rawSelect, 'agent:')
  const subnet = prefixed(rawSelect, 'subnet:')
  const select: NetworkSelect = agent ? { type: 'agent', id: agent } : subnet ? { type: 'subnet', id: subnet } : null
  return { view, q: params.get('q') ?? '', scope, layout, select }
}

export function scopeKey(scope: NetworkScope): string {
  return scope.kind === 'subnet' ? `subnet:${scope.id}` : scope.kind
}

export function serializeNetworkUrlState(state: NetworkUrlState): string {
  const params = new URLSearchParams()
  if (state.view !== 'topology') params.set('view', state.view)
  if (state.q) params.set('q', state.q)
  if (state.scope.kind !== 'all') params.set('scope', scopeKey(state.scope))
  if (state.layout !== 'list') params.set('layout', state.layout)
  if (state.select) params.set('select', `${state.select.type}:${state.select.id}`)
  return params.toString()
}

export function resolveNetworkUrlState(
  state: NetworkUrlState,
  known: { subnetIds: ReadonlySet<string>; agentIds: ReadonlySet<string> },
): NetworkUrlState {
  const scope = state.scope.kind === 'subnet' && !known.subnetIds.has(state.scope.id) ? { kind: 'all' as const } : state.scope
  const select = state.select && !(state.select.type === 'agent' ? known.agentIds : known.subnetIds).has(state.select.id)
    ? null
    : state.select
  return scope === state.scope && select === state.select ? state : { ...state, scope, select }
}
