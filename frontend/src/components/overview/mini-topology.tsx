'use client'

import { useMemo } from 'react'
import { forceCollide, forceLink, forceManyBody, forceSimulation, forceX, forceY, type SimulationNodeDatum } from 'd3-force'
import type { AgentGroup } from '@/lib/types/agent-group'
import type { NetworkAgent } from '@/components/networks/types'

interface Node extends SimulationNodeDatum { key: string; subnet: boolean; offline: boolean }
interface Link { source: string; target: string }

export function MiniTopology({ networks, agents }: { networks: readonly AgentGroup[]; agents: readonly NetworkAgent[] }) {
  const layout = useMemo(() => {
    const nodes: Node[] = [
      ...networks.map(network => ({ key: `network:${network.group_id}`, subnet: true, offline: false })),
      ...agents.map(agent => ({ key: `agent:${agent.id}`, subnet: false, offline: agent.status !== 'active' })),
    ]
    const byKey = new Map(nodes.map(node => [node.key, node]))
    const links: Link[] = networks.flatMap(network => [...new Set(network.agents)]
      .filter(id => byKey.has(`agent:${id}`))
      .map(id => ({ source: `network:${network.group_id}`, target: `agent:${id}` })))
    const simulation = forceSimulation(nodes)
      .force('link', forceLink<Node, Link>(links.map(link => ({ ...link }))).id(node => node.key).distance(18))
      .force('charge', forceManyBody().strength(-24))
      .force('x', forceX())
      .force('y', forceY())
      .force('collide', forceCollide(5))
      .stop()
    simulation.tick(300)
    const xs = nodes.map(node => node.x ?? 0)
    const ys = nodes.map(node => node.y ?? 0)
    const pad = 10
    const box = nodes.length
      ? [Math.min(...xs) - pad, Math.min(...ys) - pad, Math.max(...xs) - Math.min(...xs) + pad * 2, Math.max(...ys) - Math.min(...ys) + pad * 2]
      : [0, 0, 100, 60]
    return { nodes, links: links.map(link => ({ a: byKey.get(link.source)!, b: byKey.get(link.target)! })), viewBox: box.join(' ') }
  }, [networks, agents])

  return (
    <svg viewBox={layout.viewBox} className="h-40 w-full" aria-hidden="true">
      {layout.links.map(({ a, b }) => (
        <line key={`${a.key}-${b.key}`} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="hsl(var(--graph-edge))" strokeWidth={0.75} />
      ))}
      {layout.nodes.map(node => (
        <circle
          key={node.key}
          cx={node.x}
          cy={node.y}
          r={3}
          fill={node.offline ? 'none' : node.subnet ? 'hsl(var(--graph-subnet))' : 'hsl(var(--graph-agent))'}
          stroke={node.offline ? 'hsl(var(--graph-agent-offline))' : 'none'}
          strokeDasharray={node.offline ? '1.5 1.5' : undefined}
          strokeWidth={1}
        />
      ))}
    </svg>
  )
}
