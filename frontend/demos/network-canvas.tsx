'use client'

import { forwardRef, useImperativeHandle, useLayoutEffect, useMemo, useRef } from 'react'
import { Plus } from 'lucide-react'
import { cn } from '@/lib/utils'
import { agents, initialNetworks, type CanvasHandle, type DemoAgent, type DemoNetwork, type NetworkCanvasProps } from './network-model'

interface Point {
  x: number
  y: number
}

interface ForceNode extends Point {
  id: string
  index?: number
  vx: number
  vy: number
  fx: number | null
  fy: number | null
}

interface ForceLink {
  source: ForceNode
  target: ForceNode
}

// Only the API used from the pinned d3-force v3 browser bundle is exposed here.
interface Force {
  (alpha: number): void
  initialize?: (nodes: ForceNode[], random: () => number) => void
}

interface StrengthForce extends Force {
  strength: (strength: number) => this
}

interface LinkForce extends StrengthForce {
  id: (accessor: (node: ForceNode) => string) => this
  distance: (distance: number) => this
}

interface CollisionForce extends StrengthForce {
  iterations: (iterations: number) => this
}

interface ForceSimulation {
  stop: () => this
  restart: () => this
  tick: (iterations: number) => this
  force: (name: string, force: Force) => this
  alpha(): number
  alpha(alpha: number): this
  alphaMin: () => number
  alphaTarget: (alpha: number) => this
  alphaDecay: (decay: number) => this
  velocityDecay: (decay: number) => this
  on: (event: 'tick' | 'end', callback: (() => void) | null) => this
}

interface D3Force {
  forceSimulation: (nodes: ForceNode[]) => ForceSimulation
  forceLink: (links: ForceLink[]) => LinkForce
  forceManyBody: () => StrengthForce
  forceCollide: (radius: number) => CollisionForce
  forceX: (x: number) => StrengthForce
  forceY: (y: number) => StrengthForce
}

declare global {
  interface Window {
    d3: D3Force
  }
}

interface ClusterGeometry {
  nodes: { node: ForceNode; element: SVGGElement }[]
  links: { link: ForceLink; element: SVGLineElement }[]
  hub: SVGGElement
  empty: SVGGElement | null
}

interface Cluster {
  anchor: Point
  hub: ForceNode
  nodes: ForceNode[]
  members: Map<string, ForceNode>
  links: ForceLink[]
  simulation: ForceSimulation | null
  geometry: ClusterGeometry | null
  rx: number
  ry: number
}

interface SharedGeometry {
  a: Cluster
  b: Cluster
  source: ForceNode
  target: ForceNode
  element: SVGPathElement
}

type Drag = { pointerId: number; x: number; y: number; moved: boolean } & (
  | { kind: 'node'; networkId: string; node: ForceNode; ox: number; oy: number }
  | { kind: 'network'; networkId: string; anchor: Point; ox: number; oy: number }
  | { kind: 'empty'; networkId: string }
  | { kind: 'pan'; ox: number; oy: number }
)

type ViewportRequest = { kind: 'fit' | 'selected' } | { kind: 'focus'; id: string }

interface CanvasController extends CanvasHandle {
  update: (props: NetworkCanvasProps) => void
  destroy: () => void
}

const agentById: Record<string, DemoAgent | undefined> = Object.fromEntries(agents.map(agent => [agent.id, agent]))
const seedById: Record<string, DemoNetwork | undefined> = Object.fromEntries(initialNetworks.map(network => [network.id, network]))
const clampZoom = (scale: number) => Math.max(.2, Math.min(2.6, scale))

function measureCluster(cluster: Cluster) {
  let rx = Math.max(170, Math.abs(cluster.hub.x) + 170)
  let ry = Math.max(115, Math.abs(cluster.hub.y) + 40)
  for (const node of cluster.nodes) {
    rx = Math.max(rx, Math.abs(node.x) + 90)
    ry = Math.max(ry, Math.abs(node.y) + 40)
  }
  cluster.rx = rx
  cluster.ry = ry
}

function stopSimulation(cluster: Cluster) {
  cluster.simulation?.stop().on('tick', null).on('end', null)
  cluster.simulation = null
}

function createCanvasController(graph: SVGSVGElement, scene: SVGGElement): CanvasController {
  const parent = graph.parentElement
  if (!parent) throw new Error('NetworkCanvas requires a stage parent')
  const stage = parent
  const d3 = window.d3
  const motionPreference = window.matchMedia('(prefers-reduced-motion: reduce)')
  const listeners = new AbortController()
  const clusters = new Map<string, Cluster>()
  const pointers = new Map<number, Point>()
  let props: NetworkCanvasProps
  let sharedGeometry: SharedGeometry[] = []
  let view = { x: 0, y: 0, k: 1 }
  let drag: Drag | null = null
  let pinch: { distance: number; k: number; wx: number; wy: number } | null = null
  let geometryFrame = 0
  let viewportFrame = 0
  let viewportRequest: ViewportRequest | null = null
  let pendingReset = false
  let initialized = false
  let suspended = document.hidden
  let disposed = false
  let lastZoom: number | null = null

  function paintGeometry() {
    for (const cluster of clusters.values()) {
      const geometry = cluster.geometry
      if (!geometry) continue
      measureCluster(cluster)
      const { x, y } = cluster.anchor
      for (const { node, element } of geometry.nodes) {
        element.setAttribute('transform', `translate(${x + node.x} ${y + node.y})`)
      }
      for (const { link, element } of geometry.links) {
        element.setAttribute('x1', String(x + link.source.x))
        element.setAttribute('y1', String(y + link.source.y))
        element.setAttribute('x2', String(x + link.target.x))
        element.setAttribute('y2', String(y + link.target.y))
      }
      geometry.hub.setAttribute('transform', `translate(${x + cluster.hub.x} ${y + cluster.hub.y})`)
      geometry.empty?.setAttribute('transform', `translate(${x + cluster.hub.x} ${y + cluster.hub.y + 65})`)
    }
    for (const { a, b, source, target, element } of sharedGeometry) {
      const ax = a.anchor.x + source.x
      const ay = a.anchor.y + source.y
      const bx = b.anchor.x + target.x
      const by = b.anchor.y + target.y
      const mid = (ay + by) / 2
      element.setAttribute('d', `M${ax},${ay} C${ax},${mid} ${bx},${mid} ${bx},${by}`)
    }
  }

  function scheduleGeometry() {
    if (geometryFrame || suspended || disposed) return
    geometryFrame = requestAnimationFrame(() => {
      geometryFrame = 0
      paintGeometry()
    })
  }

  function runSimulation(simulation: ForceSimulation, alpha = .7) {
    simulation.alphaTarget(0).alpha(alpha)
    if (suspended) {
      simulation.stop()
    } else if (motionPreference.matches) {
      simulation.stop().tick(160).alpha(0)
      scheduleGeometry()
    } else {
      simulation.restart()
    }
  }

  function finishDragSimulation(reheat = true) {
    if (!drag || (drag.kind !== 'node' && drag.kind !== 'network')) return
    const cluster = clusters.get(drag.networkId)
    if (!cluster) return
    const node = drag.kind === 'node' ? drag.node : cluster.hub
    node.fx = null
    node.fy = null
    const simulation = cluster.simulation
    if (!simulation) return
    if (reheat && drag.moved) runSimulation(simulation, .55)
    else simulation.alphaTarget(0)
  }

  function cancelInteraction() {
    finishDragSimulation(false)
    drag = null
    pinch = null
    for (const id of pointers.keys()) {
      if (graph.hasPointerCapture(id)) graph.releasePointerCapture(id)
    }
    pointers.clear()
    graph.classList.remove('dragging')
  }

  function placeCluster(cluster: Cluster, occupied: Cluster[]) {
    measureCluster(cluster)
    let overlaps = false
    let right = -Infinity
    for (const other of occupied) {
      measureCluster(other)
      right = Math.max(right, other.anchor.x + other.rx)
      if (Math.hypot(
        (cluster.anchor.x - other.anchor.x) / (cluster.rx + other.rx + 60),
        (cluster.anchor.y - other.anchor.y) / (cluster.ry + other.ry + 80),
      ) < 1) overlaps = true
    }
    if (overlaps) cluster.anchor.x = right + cluster.rx + 100
  }

  function reconcileSimulations(reset = false) {
    if (reset) {
      cancelInteraction()
      for (const cluster of clusters.values()) stopSimulation(cluster)
      clusters.clear()
    }
    const ids = new Set(props.networks.map(network => network.id))
    for (const [id, cluster] of clusters) {
      if (ids.has(id)) continue
      if (drag && drag.kind !== 'pan' && drag.networkId === id) cancelInteraction()
      stopSimulation(cluster)
      clusters.delete(id)
    }
    const added: Cluster[] = []
    for (const network of props.networks) {
      let cluster = clusters.get(network.id)
      if (!cluster) {
        const position = reset ? seedById[network.id] ?? network : network
        cluster = {
          anchor: { x: position.x, y: position.y }, nodes: [], members: new Map(),
          hub: { id: `network:${network.id}`, x: 0, y: 0, vx: 0, vy: 0, fx: null, fy: null },
          links: [], simulation: null, geometry: null, rx: 247, ry: 184,
        }
        clusters.set(network.id, cluster)
        added.push(cluster)
      }
      if (cluster.nodes.length === network.members.length && cluster.nodes.every((node, index) => node.id === network.members[index])) continue
      if (drag && drag.kind !== 'pan' && drag.networkId === network.id) cancelInteraction()
      stopSimulation(cluster)
      const previous = cluster.members
      cluster.nodes = network.members.map((id, index) => previous.get(id) ?? {
        id, x: Math.cos(index * 2.399963) * 55, y: Math.sin(index * 2.399963) * 45,
        vx: 0, vy: 0, fx: null, fy: null,
      })
      cluster.members = new Map(cluster.nodes.map(node => [node.id, node]))
      cluster.links = cluster.nodes.map(node => ({ source: cluster.hub, target: node }))
      if (!cluster.nodes.length) continue
      const simulation = d3.forceSimulation([cluster.hub, ...cluster.nodes]).stop()
        .force('link', d3.forceLink(cluster.links).id(node => node.id).distance(155).strength(.3))
        .force('charge', d3.forceManyBody().strength(-700))
        .force('collision', d3.forceCollide(60).strength(.85).iterations(2))
        .force('x', d3.forceX(0).strength(.025))
        .force('y', d3.forceY(0).strength(.025))
        .alphaDecay(.045).velocityDecay(.28)
        .on('tick', scheduleGeometry).on('end', scheduleGeometry)
      cluster.simulation = simulation
      // Measure new clusters at their settled size before reserving their canvas space.
      if (added.includes(cluster)) simulation.tick(160)
      runSimulation(simulation)
    }
    const newClusters = new Set(added)
    const occupied = Array.from(clusters.values()).filter(cluster => !newClusters.has(cluster))
    // Reserve the original clusters first, regardless of the parent's list ordering.
    for (const network of initialNetworks) {
      const cluster = clusters.get(network.id)
      if (!cluster || !newClusters.delete(cluster)) continue
      placeCluster(cluster, occupied)
      occupied.push(cluster)
    }
    for (const cluster of newClusters) {
      placeCluster(cluster, occupied)
      occupied.push(cluster)
    }
  }

  function cacheGeometry() {
    for (const root of scene.querySelectorAll<SVGGElement>('.cluster')) {
      const cluster = clusters.get(root.dataset.cluster ?? '')
      if (!cluster) continue
      const nodes: ClusterGeometry['nodes'] = []
      for (const element of root.querySelectorAll<SVGGElement>('.agent-node')) {
        const node = cluster.members.get(element.dataset.agent ?? '')
        if (node) nodes.push({ node, element })
      }
      const lines = root.querySelectorAll<SVGLineElement>('.edge')
      cluster.geometry = {
        nodes,
        links: cluster.links.map((link, index) => ({ link, element: lines[index] })),
        hub: root.querySelector<SVGGElement>('.cluster-title')!,
        empty: root.querySelector<SVGGElement>('.empty-node'),
      }
    }
    sharedGeometry = []
    for (const element of scene.querySelectorAll<SVGPathElement>('.shared-edge')) {
      const a = clusters.get(element.dataset.sharedFrom ?? '')
      const b = clusters.get(element.dataset.sharedTo ?? '')
      const id = element.dataset.sharedAgent ?? ''
      const source = a?.members.get(id)
      const target = b?.members.get(id)
      if (a && b && source && target) sharedGeometry.push({ a, b, source, target, element })
    }
  }

  function applyTransform() {
    scene.setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.k})`)
    const percent = Math.round(view.k * 100)
    if (percent !== lastZoom) {
      lastZoom = percent
      props.onZoomChange(percent)
    }
  }

  function visibleArea() {
    const mobile = window.innerWidth <= 720
    return {
      w: Math.max(1, stage.clientWidth - (!mobile && props.selection ? 310 : 0)),
      h: Math.max(1, stage.clientHeight - (mobile && props.selection ? Math.min(stage.clientHeight * .46, 380) : 0)),
    }
  }

  function fitAll() {
    if (!clusters.size) {
      view = { x: 0, y: 0, k: 1 }
      applyTransform()
      return
    }
    let left = Infinity
    let right = -Infinity
    let top = Infinity
    let bottom = -Infinity
    for (const cluster of clusters.values()) {
      measureCluster(cluster)
      left = Math.min(left, cluster.anchor.x - cluster.rx - 25)
      right = Math.max(right, cluster.anchor.x + cluster.rx + 25)
      top = Math.min(top, cluster.anchor.y - cluster.ry - 55)
      bottom = Math.max(bottom, cluster.anchor.y + cluster.ry + 65)
    }
    const { w, h } = visibleArea()
    const k = Math.max(.2, Math.min((w - 55) / (right - left), (h - 110) / (bottom - top), 1.2))
    view = { k, x: w / 2 - (left + right) / 2 * k, y: (h - 10) / 2 - (top + bottom) / 2 * k }
    applyTransform()
  }

  function focusNetwork(id: string) {
    const cluster = clusters.get(id)
    if (!cluster) return
    measureCluster(cluster)
    const { w, h } = visibleArea()
    const k = Math.max(.3, Math.min((w - 45) / (cluster.rx * 2 + 60), (h - 110) / (cluster.ry * 2 + 90), 1.15))
    view = { k, x: w / 2 - cluster.anchor.x * k, y: Math.max(90, h / 2) - cluster.anchor.y * k }
    applyTransform()
  }

  function initialViewport() {
    if (window.innerWidth <= 720 && props.networks.length) focusNetwork(props.networks[0].id)
    else fitAll()
  }

  function scheduleViewport(request: ViewportRequest) {
    viewportRequest = request
    if (viewportFrame || suspended || disposed) return
    viewportFrame = requestAnimationFrame(() => {
      viewportFrame = 0
      if (pendingReset) {
        pendingReset = false
        reconcileSimulations(true)
        cacheGeometry()
        paintGeometry()
      }
      const next = viewportRequest
      viewportRequest = null
      if (next?.kind === 'focus') focusNetwork(next.id)
      else if (next?.kind === 'fit') fitAll()
      else if (next?.kind === 'selected' && props.selection) focusNetwork(props.selection.networkId)
      else initialViewport()
    })
  }

  function zoom(factor: number, cx = stage.clientWidth / 2, cy = stage.clientHeight / 2) {
    const k = clampZoom(view.k * factor)
    const worldX = (cx - view.x) / view.k
    const worldY = (cy - view.y) / view.k
    view = { k, x: cx - worldX * k, y: cy - worldY * k }
    applyTransform()
  }

  function choose(networkId: string, agentId: string | null = null, focus = false) {
    props.onSelect({ networkId, agentId })
    if (focus || window.innerWidth <= 720) scheduleViewport({ kind: 'focus', id: networkId })
  }

  function localPoint(event: MouseEvent | PointerEvent): Point {
    const rect = graph.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }

  function pointerDistance() {
    const [a, b] = pointers.values()
    return { distance: Math.hypot(a.x - b.x, a.y - b.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
  }

  function onPointerDown(event: PointerEvent) {
    if (event.button !== 0 || pointers.size >= 2) return
    const p = localPoint(event)
    pointers.set(event.pointerId, p)
    graph.setPointerCapture(event.pointerId)
    if (pointers.size === 2) {
      finishDragSimulation()
      const distance = pointerDistance()
      pinch = { distance: distance.distance, k: view.k, wx: (distance.x - view.x) / view.k, wy: (distance.y - view.y) / view.k }
      drag = null
      return
    }
    const target = event.target instanceof Element ? event.target : graph
    const nodeElement = target.closest<SVGGElement>('[data-agent]')
    const clusterElement = target.closest<SVGElement>('[data-drag-network]')
    const empty = target.closest<SVGGElement>('[data-add-network]')
    const start = { pointerId: event.pointerId, x: p.x, y: p.y, moved: false }
    const nodeNetworkId = nodeElement?.dataset.network
    const node = nodeNetworkId ? clusters.get(nodeNetworkId)?.members.get(nodeElement?.dataset.agent ?? '') : undefined
    const networkId = clusterElement?.dataset.dragNetwork
    const cluster = networkId ? clusters.get(networkId) : undefined
    if (node && nodeNetworkId) {
      node.fx = node.x
      node.fy = node.y
      drag = { ...start, kind: 'node', networkId: nodeNetworkId, node, ox: node.x, oy: node.y }
    } else if (cluster && networkId) {
      cluster.hub.fx = cluster.hub.x
      cluster.hub.fy = cluster.hub.y
      drag = { ...start, kind: 'network', networkId, anchor: cluster.anchor, ox: cluster.anchor.x, oy: cluster.anchor.y }
    } else if (empty?.dataset.addNetwork) {
      drag = { ...start, kind: 'empty', networkId: empty.dataset.addNetwork }
    } else {
      drag = { ...start, kind: 'pan', ox: view.x, oy: view.y }
    }
    graph.classList.add('dragging')
  }

  function onPointerMove(event: PointerEvent) {
    if (!pointers.has(event.pointerId)) return
    const p = localPoint(event)
    pointers.set(event.pointerId, p)
    if (pinch && pointers.size === 2) {
      const distance = pointerDistance()
      const k = clampZoom(pinch.k * distance.distance / Math.max(pinch.distance, 1))
      view = { k, x: distance.x - pinch.wx * k, y: distance.y - pinch.wy * k }
      applyTransform()
      return
    }
    if (!drag || drag.pointerId !== event.pointerId) return
    const dx = p.x - drag.x
    const dy = p.y - drag.y
    if (Math.hypot(dx, dy) > 4) drag.moved = true
    if (!drag.moved) return
    if (drag.kind === 'pan') {
      view.x = drag.ox + dx
      view.y = drag.oy + dy
      applyTransform()
    } else if (drag.kind === 'network') {
      const x = drag.ox + dx / view.k
      const y = drag.oy + dy / view.k
      if (!motionPreference.matches && !suspended) {
        // Shift the coordinate origin, not the particles: they trail the hub
        // and catch up through D3's spring forces instead of moving rigidly.
        const cluster = clusters.get(drag.networkId)!
        const shiftX = x - drag.anchor.x
        const shiftY = y - drag.anchor.y
        for (const node of cluster.nodes) {
          node.x -= shiftX
          node.y -= shiftY
        }
      }
      drag.anchor.x = x
      drag.anchor.y = y
      scheduleGeometry()
    } else if (drag.kind === 'node') {
      drag.node.fx = drag.node.x = drag.ox + dx / view.k
      drag.node.fy = drag.node.y = drag.oy + dy / view.k
      scheduleGeometry()
    }
    if ((drag.kind === 'node' || drag.kind === 'network') && !motionPreference.matches && !suspended) {
      const simulation = clusters.get(drag.networkId)?.simulation
      simulation?.alpha(Math.max(simulation.alpha(), .35)).alphaTarget(.22).restart()
    }
  }

  function releasePointer(event: PointerEvent) {
    if (!pointers.delete(event.pointerId)) return
    if (graph.hasPointerCapture(event.pointerId)) graph.releasePointerCapture(event.pointerId)
    if (pinch) {
      pinch = null
      drag = null
      graph.classList.remove('dragging')
      return
    }
    if (drag?.pointerId !== event.pointerId) return
    finishDragSimulation()
    const released = drag
    drag = null
    graph.classList.remove('dragging')
    if (!released.moved && event.type === 'pointerup') {
      if (released.kind === 'node') choose(released.networkId, released.node.id)
      else if (released.kind === 'network') choose(released.networkId)
      else if (released.kind === 'empty') {
        choose(released.networkId)
        props.onAddAgent(released.networkId)
      } else props.onSelect(null)
    }
  }

  function activateTarget(target: EventTarget | null) {
    if (!(target instanceof Element)) return
    const node = target.closest<SVGGElement>('[data-agent]')
    const network = target.closest<SVGGElement>('[data-select-network]')
    const empty = target.closest<SVGGElement>('[data-add-network]')
    if (node?.dataset.network && node.dataset.agent) choose(node.dataset.network, node.dataset.agent)
    else if (network?.dataset.selectNetwork) choose(network.dataset.selectNetwork)
    else if (empty?.dataset.addNetwork) {
      choose(empty.dataset.addNetwork)
      props.onAddAgent(empty.dataset.addNetwork)
    }
  }

  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      activateTarget(event.target)
      return
    }
    if (event.key === 'Escape') {
      cancelInteraction()
      props.onSelect(null)
      return
    }
    if (event.target !== graph) return
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-', '0'].includes(event.key)) return
    event.preventDefault()
    if (event.key === '+' || event.key === '=') zoom(1.2)
    else if (event.key === '-') zoom(1 / 1.2)
    else if (event.key === '0') scheduleViewport({ kind: 'fit' })
    else {
      view.x += event.key === 'ArrowLeft' ? 40 : event.key === 'ArrowRight' ? -40 : 0
      view.y += event.key === 'ArrowUp' ? 40 : event.key === 'ArrowDown' ? -40 : 0
      applyTransform()
    }
  }

  function suspend() {
    suspended = true
    cancelInteraction()
    for (const cluster of clusters.values()) cluster.simulation?.stop()
    cancelAnimationFrame(geometryFrame)
    cancelAnimationFrame(viewportFrame)
    geometryFrame = viewportFrame = 0
  }

  function resume() {
    if (document.hidden || disposed) return
    suspended = false
    for (const cluster of clusters.values()) {
      const simulation = cluster.simulation
      if (!simulation || simulation.alpha() <= simulation.alphaMin()) continue
      if (motionPreference.matches) simulation.stop().tick(160).alpha(0)
      else simulation.restart()
    }
    scheduleGeometry()
    if (viewportRequest) scheduleViewport(viewportRequest)
  }

  const { signal } = listeners
  graph.addEventListener('pointerdown', onPointerDown, { signal })
  graph.addEventListener('pointermove', onPointerMove, { signal })
  graph.addEventListener('pointerup', releasePointer, { signal })
  graph.addEventListener('pointercancel', releasePointer, { signal })
  graph.addEventListener('lostpointercapture', releasePointer, { signal })
  graph.addEventListener('keydown', onKeyDown, { signal })
  graph.addEventListener('click', event => {
    if (event.detail === 0) activateTarget(event.target)
  }, { signal })
  graph.addEventListener('wheel', event => {
    event.preventDefault()
    const p = localPoint(event)
    const delta = event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage.clientHeight : 1)
    zoom(Math.exp(-delta * .0015), p.x, p.y)
  }, { passive: false, signal })
  graph.addEventListener('dblclick', event => {
    if (!(event.target instanceof Element)) return
    const node = event.target.closest<SVGGElement>('[data-agent]')
    const cluster = event.target.closest<SVGElement>('[data-drag-network]')
    const id = node?.dataset.network ?? cluster?.dataset.dragNetwork
    if (id) choose(id, null, true)
  }, { signal })
  motionPreference.addEventListener('change', () => {
    for (const cluster of clusters.values()) {
      if (cluster.simulation) runSimulation(cluster.simulation)
    }
  }, { signal })
  document.addEventListener('visibilitychange', () => document.hidden ? suspend() : resume(), { signal })
  window.addEventListener('pagehide', suspend, { signal })
  window.addEventListener('pageshow', resume, { signal })
  const observer = new ResizeObserver(() => {
    if (initialized && !viewportRequest) scheduleViewport({ kind: 'selected' })
  })
  observer.observe(stage)

  return {
    update(next) {
      const previousSelection = initialized ? props.selection : null
      props = next
      reconcileSimulations()
      cacheGeometry()
      paintGeometry()
      if (!initialized) {
        initialized = true
        scheduleViewport({ kind: 'selected' })
      } else if (window.innerWidth <= 720 && props.selection && previousSelection?.networkId !== props.selection.networkId) {
        scheduleViewport({ kind: 'focus', id: props.selection.networkId })
      }
    },
    fitAll: () => scheduleViewport({ kind: 'fit' }),
    focusNetwork: id => scheduleViewport({ kind: 'focus', id }),
    zoom: factor => zoom(factor),
    resetLayout() {
      pendingReset = true
      scheduleViewport({ kind: 'selected' })
    },
    destroy() {
      disposed = true
      listeners.abort()
      observer.disconnect()
      suspend()
      for (const cluster of clusters.values()) stopSimulation(cluster)
      clusters.clear()
      sharedGeometry = []
    },
  }
}

export const NetworkCanvas = forwardRef<CanvasHandle, NetworkCanvasProps>(function NetworkCanvas(props, ref) {
  const graphRef = useRef<SVGSVGElement>(null)
  const sceneRef = useRef<SVGGElement>(null)
  const controllerRef = useRef<CanvasController | null>(null)
  const { networks, selection, showShared } = props
  const topology = useMemo(() => {
    const memberships = new Map<string, string[]>()
    for (const network of networks) {
      for (let i = 0; i < network.members.length; i++) {
        const id = network.members[i]
        const existing = memberships.get(id)
        if (existing) existing.push(network.id)
        else memberships.set(id, [network.id])
      }
    }
    const shared: { agentId: string; from: string; to: string }[] = []
    for (const [agentId, instances] of memberships) {
      for (let i = 1; i < instances.length; i++) shared.push({ agentId, from: instances[0], to: instances[i] })
    }
    return { memberships, shared }
  }, [networks])

  useLayoutEffect(() => {
    const graph = graphRef.current
    const scene = sceneRef.current
    if (!graph || !scene) return
    const controller = createCanvasController(graph, scene)
    controllerRef.current = controller
    return () => {
      controllerRef.current = null
      controller.destroy()
    }
  }, [])

  useLayoutEffect(() => {
    controllerRef.current?.update(props)
  }, [props])

  useImperativeHandle(ref, () => ({
    fitAll: () => controllerRef.current?.fitAll(),
    focusNetwork: id => controllerRef.current?.focusNetwork(id),
    zoom: factor => controllerRef.current?.zoom(factor),
    resetLayout: () => controllerRef.current?.resetLayout(),
  }), [])

  return (
    <svg
      ref={graphRef}
      className="graph"
      id="graph"
      role="group"
      aria-label="Network 关系画布，可拖动画布、节点和网络标题；方向键平移，加减键缩放，0 显示全部"
      tabIndex={0}
    >
      <g ref={sceneRef} id="scene">
        {showShared && topology.shared.map(link => (
          <path
            key={`${link.agentId}:${link.from}:${link.to}`}
            className={cn('shared-edge', selection?.agentId === link.agentId && 'related')}
            data-shared-agent={link.agentId}
            data-shared-from={link.from}
            data-shared-to={link.to}
            aria-hidden="true"
          />
        ))}
        {networks.map(network => (
          <g
            key={network.id}
            className={cn('cluster', selection?.networkId === network.id && 'selected')}
            data-cluster={network.id}
          >
            {network.members.map(id => <line key={id} className="edge" aria-hidden="true" />)}
            <g
              className="cluster-title"
              data-drag-network={network.id}
              data-select-network={network.id}
              role="button"
              tabIndex={0}
              aria-label={`打开 ${network.name}，${network.members.length} 个 Agent`}
              aria-pressed={selection?.networkId === network.id}
            >
              <title>{network.name} — 拖动可移动整个 Network</title>
              <circle className="node-hit" r={24} />
              <circle className="network-dot" r={6} />
              <text className="cluster-name" y={-19}>{network.name.length > 20 ? `${network.name.slice(0, 19)}…` : network.name}</text>
            </g>
            {network.members.map(id => {
              const agent = agentById[id]
              if (!agent) return null
              const count = topology.memberships.get(id)?.length ?? 1
              return (
                <g
                  key={id}
                  className={cn('agent-node', agent.status === 'inactive' && 'inactive', selection?.agentId === id && 'selected')}
                  data-agent={id}
                  data-network={network.id}
                  role="button"
                  tabIndex={0}
                  aria-label={`${agent.name}，位于${network.name}`}
                  aria-pressed={selection?.networkId === network.id && selection.agentId === id}
                >
                  <title>{agent.name} · {agent.description}{agent.status === 'inactive' ? ' · Inactive' : ''}{count > 1 ? ` · 同时位于 ${count} 个 Networks` : ''}</title>
                  <circle className="node-hit" r={22} />
                  <circle className="node-dot" r={4.5} />
                  <text className="node-name" y={22}>{agent.name}</text>
                </g>
              )
            })}
            {network.members.length === 0 && (
              <g className="empty-node" data-add-network={network.id} role="button" tabIndex={0} aria-label={`为${network.name}添加 Agent`}>
                <circle r={14} />
                <Plus x={-7} y={-7} width={14} height={14} stroke="currentColor" strokeWidth={1.5} aria-hidden="true" />
                <text y={34}>添加 Agent</text>
              </g>
            )}
          </g>
        ))}
      </g>
    </svg>
  )
})
