'use client'

import { forwardRef, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { placeholderAgent } from '@/lib/network-agent'
import { buildNeighbors, emphasisFor, isLabelVisible } from '@/lib/graph-labels'
import { createNetworkCanvasController, type NetworkCanvasController } from './network-canvas-controller'
import type { NetworkCanvasHandle, NetworkCanvasProps } from './types'
import './networks.css'

function shortenLabel(label: string, limit: number) {
  const characters = Array.from(label)
  return characters.length > limit ? `${characters.slice(0, limit - 1).join('')}…` : label
}

export const NetworkCanvas = forwardRef<NetworkCanvasHandle, NetworkCanvasProps>(function NetworkCanvas(props, ref) {
  const graphRef = useRef<SVGSVGElement>(null)
  const sceneRef = useRef<SVGGElement>(null)
  const controllerRef = useRef<NetworkCanvasController | null>(null)
  const { networks, agents, selection, search, display, onSelect, onZoomChange } = props
  const [hovered, setHovered] = useState<string | null>(null)
  const topology = useMemo(() => {
    const uniqueNetworks = new Map(networks.map((network) => [network.group_id, network]))
    const uniqueAgents = new Map(agents.map((agent) => [agent.id, agent]))
    const memberships: { key: string; networkId: string; agentId: string }[] = []
    for (const network of uniqueNetworks.values()) {
      for (const id of new Set(network.agents)) {
        if (!uniqueAgents.has(id)) {
          uniqueAgents.set(id, placeholderAgent(id, '', 'unknown'))
        }
        memberships.push({ key: JSON.stringify([network.group_id, id]), networkId: network.group_id, agentId: id })
      }
    }
    return { networks: Array.from(uniqueNetworks.values()), agents: Array.from(uniqueAgents.values()), memberships }
  }, [networks, agents])
  const neighbors = useMemo(() => buildNeighbors(topology.memberships), [topology])
  const selectedKey = selection ? `${selection.type}:${selection.id}` : null
  const emphasis = emphasisFor({ hovered, selected: selectedKey, search, neighbors })
  const dimmed = (key: string) => Boolean(emphasis && !emphasis.nodes.has(key))
  const related = (key: string) => Boolean(emphasis?.nodes.has(key) && !emphasis.anchors.has(key))
  const edgeOn = (networkId: string, agentId: string) =>
    Boolean(emphasis && (emphasis.anchors.has(`network:${networkId}`) || emphasis.anchors.has(`agent:${agentId}`)))
  const labelClass = (key: string) =>
    isLabelVisible(key, { hovered, selected: selectedKey, prefs: display, neighbors }) ? undefined : 'label-hidden'
  const hoverHandlers = (key: string) => ({
    onPointerEnter: () => setHovered(key),
    onPointerLeave: () => setHovered((current) => (current === key ? null : current)),
    onFocus: () => setHovered(key),
    onBlur: () => setHovered((current) => (current === key ? null : current)),
  })

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
    controllerRef.current?.update({ networks, agents, selection, search, display, onSelect, onZoomChange })
  }, [networks, agents, selection, search, display, onSelect, onZoomChange])

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
      aria-label="Subnet graph. Teal nodes are subnets; gray nodes are agents; hollow nodes are offline. Lines show memberships. Drag to adjust the layout. Tab to a node and press Enter for details. Arrow keys pan; plus and minus zoom; Escape clears; zero fits all nodes."
    >
      <g ref={sceneRef} className="network-scene">
        <g className="network-edges" aria-hidden="true">
          {topology.memberships.map((edge) => (
            <line
              key={edge.key}
              className={cn(
                'network-edge network-member-edge',
                edgeOn(edge.networkId, edge.agentId) && 'selected',
                emphasis && !edgeOn(edge.networkId, edge.agentId) && 'dimmed'
              )}
              data-network-id={edge.networkId}
              data-agent-id={edge.agentId}
            />
          ))}
        </g>
        {topology.agents.map((agent) => {
          const key = `agent:${agent.id}`
          const selected = selection?.type === 'agent' && selection.id === agent.id
          const stateDescription =
            agent.status === 'unknown'
              ? 'Information not confirmed'
              : agent.status === 'active'
                ? ''
                : 'Currently offline'
          return (
            <g
              key={key}
              className={cn(
                'network-agent',
                agent.status !== 'active' && 'inactive',
                selected && 'selected',
                related(key) && 'related',
                dimmed(key) && 'dimmed'
              )}
              data-node-key={key}
              data-agent-id={agent.id}
              data-status={agent.status}
              role="button"
              tabIndex={0}
              aria-label={`${agent.name || agent.id}, Agent${stateDescription ? `, ${stateDescription}` : ''}. Drag to pull connected nodes.`}
              aria-pressed={selected}
              {...hoverHandlers(key)}
            >
              <title>
                {agent.name || agent.id}
                {agent.description ? ` — ${agent.description}` : ''}
                {stateDescription ? ` — ${stateDescription}` : ''}
              </title>
              <circle className="network-node-hit" r={24} />
              <circle className="network-agent-dot" r={6} />
              <text className={cn('network-agent-label', labelClass(key))} y={-12}>
                {shortenLabel(agent.name || agent.id, 32)}
              </text>
            </g>
          )
        })}
        {topology.networks.map((network) => {
          const key = `network:${network.group_id}`
          const selected = selection?.type === 'network' && selection.id === network.group_id
          const count = new Set(network.agents).size
          return (
            <g
              key={key}
              className={cn(
                'network-hub',
                selected && 'selected',
                related(key) && 'related',
                dimmed(key) && 'dimmed'
              )}
              data-node-key={key}
              data-network-id={network.group_id}
              role="button"
              tabIndex={0}
              aria-label={`${network.name}, Subnet, ${count} ${count === 1 ? 'Agent' : 'Agents'}. Drag to pull connected nodes.`}
              aria-pressed={selected}
              {...hoverHandlers(key)}
            >
              <title>
                {network.name} — {count} {count === 1 ? 'Agent' : 'Agents'}
              </title>
              <circle className="network-node-hit" r={24} />
              <circle className="network-hub-dot" r={6} />
              <text className={cn('network-hub-label', labelClass(key))} y={-14}>
                {shortenLabel(network.name, 28)}
              </text>
            </g>
          )
        })}
      </g>
    </svg>
  )
})
