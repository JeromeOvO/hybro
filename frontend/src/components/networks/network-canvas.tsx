'use client'

import { forwardRef, useImperativeHandle, useLayoutEffect, useMemo, useRef } from 'react'
import { cn } from '@/lib/utils'
import { createNetworkCanvasController, type NetworkCanvasController } from './network-canvas-controller'
import type { NetworkAgent, NetworkCanvasHandle, NetworkCanvasProps } from './types'
import './networks.css'

function shortenLabel(label: string, limit: number) {
  const characters = Array.from(label)
  return characters.length > limit ? `${characters.slice(0, limit - 1).join('')}…` : label
}

export const NetworkCanvas = forwardRef<NetworkCanvasHandle, NetworkCanvasProps>(function NetworkCanvas(props, ref) {
  const graphRef = useRef<SVGSVGElement>(null)
  const sceneRef = useRef<SVGGElement>(null)
  const controllerRef = useRef<NetworkCanvasController | null>(null)
  const { networks, agents, selection, onSelect, onZoomChange } = props
  const topology = useMemo(() => {
    const uniqueNetworks = new Map(networks.map((network) => [network.group_id, network]))
    const uniqueAgents = new Map(agents.map((agent) => [agent.id, agent]))
    const memberships: { key: string; networkId: string; agentId: string }[] = []
    for (const network of uniqueNetworks.values()) {
      for (const id of new Set(network.agents)) {
        if (!uniqueAgents.has(id)) {
          const member: NetworkAgent = { id, name: id, description: '', skills: [], status: 'unknown' }
          uniqueAgents.set(id, member)
        }
        memberships.push({ key: JSON.stringify([network.group_id, id]), networkId: network.group_id, agentId: id })
      }
    }
    return { networks: Array.from(uniqueNetworks.values()), agents: Array.from(uniqueAgents.values()), memberships }
  }, [networks, agents])
  const related = useMemo(() => {
    const networkIds = new Set<string>()
    const agentIds = new Set<string>()
    for (const edge of topology.memberships) {
      if (selection?.type === 'network' && selection.id === edge.networkId) agentIds.add(edge.agentId)
      if (selection?.type === 'agent' && selection.id === edge.agentId) networkIds.add(edge.networkId)
    }
    return { networkIds, agentIds }
  }, [topology, selection])

  useLayoutEffect(() => {
    const graph = graphRef.current
    const scene = sceneRef.current
    if (!graph || !scene) return
    const controller = createNetworkCanvasController(graph, scene)
    controllerRef.current = controller
    return () => {
      controllerRef.current = null
      controller.destroy()
    }
  }, [])

  useLayoutEffect(() => {
    controllerRef.current?.update({ networks, agents, selection, onSelect, onZoomChange })
  }, [networks, agents, selection, onSelect, onZoomChange])

  useImperativeHandle(
    ref,
    () => ({
      fitAll: () => controllerRef.current?.fitAll(),
      focusNetwork: (id) => controllerRef.current?.focusNetwork(id),
      zoom: (factor) => controllerRef.current?.zoom(factor),
      resetLayout: () => controllerRef.current?.resetLayout()
    }),
    []
  )

  return (
    <svg
      ref={graphRef}
      className="network-graph"
      role="group"
      tabIndex={0}
      aria-label="Network graph. Blue nodes are Networks; grey nodes are Agents. Lines show memberships. Drag a node to move it or the background to pan. Tab to a node and press Enter to select. Arrow keys pan; plus and minus zoom; Escape clears selection; zero fits all nodes."
    >
      <g ref={sceneRef} className="network-scene">
        <g className="network-edges" aria-hidden="true">
          {topology.memberships.map((edge) => (
            <line
              key={edge.key}
              className={cn(
                'network-edge network-member-edge',
                ((selection?.type === 'network' && selection.id === edge.networkId) ||
                  (selection?.type === 'agent' && selection.id === edge.agentId)) &&
                  'selected'
              )}
              data-network-id={edge.networkId}
              data-agent-id={edge.agentId}
            />
          ))}
        </g>
        {topology.agents.map((agent) => {
          const selected = selection?.type === 'agent' && selection.id === agent.id
          const stateDescription =
            agent.status === 'unknown'
              ? 'Information not confirmed'
              : agent.status === 'active'
                ? ''
                : 'Currently unavailable'
          return (
            <g
              key={`agent:${agent.id}`}
              className={cn(
                'network-agent',
                agent.status !== 'active' && 'inactive',
                selected && 'selected',
                related.agentIds.has(agent.id) && 'related'
              )}
              data-node-key={`agent:${agent.id}`}
              data-agent-id={agent.id}
              data-status={agent.status}
              role="button"
              tabIndex={0}
              aria-label={`${agent.name || agent.id}, Agent${stateDescription ? `, ${stateDescription}` : ''}. Drag to pull connected nodes.`}
              aria-pressed={selected}
            >
              <title>
                {agent.name || agent.id}
                {agent.description ? ` — ${agent.description}` : ''}
                {stateDescription ? ` — ${stateDescription}` : ''}
              </title>
              <circle className="network-node-hit" r={18} />
              <circle className="network-agent-dot" r={5} />
              <text className="network-agent-label" y={23}>
                {shortenLabel(agent.name || agent.id, 32)}
              </text>
            </g>
          )
        })}
        {topology.networks.map((network) => {
          const selected = selection?.type === 'network' && selection.id === network.group_id
          const count = new Set(network.agents).size
          return (
            <g
              key={`network:${network.group_id}`}
              className={cn(
                'network-hub',
                selected && 'selected',
                related.networkIds.has(network.group_id) && 'related'
              )}
              data-node-key={`network:${network.group_id}`}
              data-network-id={network.group_id}
              role="button"
              tabIndex={0}
              aria-label={`${network.name}, Network, ${count} ${count === 1 ? 'Agent' : 'Agents'}. Drag to pull connected nodes.`}
              aria-pressed={selected}
            >
              <title>
                {network.name} — {count} {count === 1 ? 'Agent' : 'Agents'}
              </title>
              <circle className="network-node-hit" r={20} />
              <circle className="network-hub-dot" r={7} />
              <text className="network-hub-label" y={-19}>
                {shortenLabel(network.name, 28)}
              </text>
            </g>
          )
        })}
      </g>
    </svg>
  )
})
