import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationNodeDatum
} from 'd3-force'
import type { NetworkCanvasHandle, NetworkCanvasProps, NetworkSelection } from './types'

interface Point {
  x: number
  y: number
}
interface View extends Point {
  k: number
}
interface ForceNode extends SimulationNodeDatum {
  key: string
  selection: NetworkSelection
  x: number
  y: number
  element: SVGGElement
  labelHalfWidth: number
}
interface ForceLink {
  source: ForceNode
  target: ForceNode
  element: SVGLineElement
}
type Drag = { pointerId: number; x: number; y: number; moved: boolean; ox: number; oy: number } & (
  | { kind: 'node'; node: ForceNode }
  | { kind: 'pan' }
)

export interface NetworkCanvasController extends NetworkCanvasHandle {
  update: (props: NetworkCanvasProps) => void
  destroy: () => void
}
const INITIAL_ZOOM = 1.41


export function createNetworkCanvasController(graph: SVGSVGElement, scene: SVGGElement): NetworkCanvasController {
  const stage = graph.parentElement
  if (!stage) throw new Error('NetworkCanvas requires a stage parent')
  const listeners = new AbortController()
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
  const pointers = new Map<number, Point>()
  let nodesByKey = new Map<string, ForceNode>()
  let linksByKey = new Map<string, ForceLink>()
  let nodes: ForceNode[] = []
  let links: ForceLink[] = []
  let props: NetworkCanvasProps | undefined
  let details: HTMLElement | null = null
  let focusedKey: string | null = null
  let preferredScale: number | null = INITIAL_ZOOM
  let view: View = { x: 0, y: 0, k: INITIAL_ZOOM }
  let minimumZoom = 0.05
  let animation: { started: number; from: View; to: View } | null = null
  let frame = 0
  let suspended = document.hidden
  let disposed = false
  let initialized = false
  let lastZoom = 0
  let drag: Drag | null = null
  let pinch: { distance: number; k: number; wx: number; wy: number } | null = null
  const linkForce = forceLink<ForceNode, ForceLink>()
    .id((node) => node.key)
    .distance(85)
  const simulation = forceSimulation<ForceNode, ForceLink>([])
    .stop()
    .force('link', linkForce)
    .force('charge', forceManyBody<ForceNode>().strength(-180))
    .force('x', forceX<ForceNode>().strength(0.045))
    .force('y', forceY<ForceNode>().strength(0.045))
    .force(
      'collision',
      forceCollide<ForceNode>((node) => (node.selection.type === 'network' ? 24 : 18))
    )
    .on('tick', requestPaint)
    .on('end', requestPaint)

  function notifyZoom() {
    const percent = Math.round(view.k * 100)
    if (lastZoom !== percent && props) {
      lastZoom = percent
      props.onZoomChange(percent)
    }
  }
  function requestPaint() {
    if (!frame && !suspended && !disposed) frame = requestAnimationFrame(renderFrame)
  }
  function runSimulation() {
    if (suspended || disposed || !nodes.length) {
      simulation.stop()
    } else if (motion.matches) {
      simulation.stop().tick(drag?.kind === 'node' ? 12 : 300)
      if (drag?.kind !== 'node') simulation.alpha(0)
      requestPaint()
    } else if (simulation.alpha() > simulation.alphaMin() || simulation.alphaTarget() > 0) {
      simulation.restart()
    }
  }
  function paint() {
    scene.setAttribute('transform', `translate(${view.x} ${view.y}) scale(${view.k})`)
    for (const node of nodes) node.element.setAttribute('transform', `translate(${node.x} ${node.y})`)
    for (const link of links) {
      link.element.setAttribute('x1', String(link.source.x))
      link.element.setAttribute('y1', String(link.source.y))
      link.element.setAttribute('x2', String(link.target.x))
      link.element.setAttribute('y2', String(link.target.y))
    }
  }
  function renderFrame(now: number) {
    frame = 0
    if (suspended || disposed) return
    if (animation) {
      const t = Math.min(1, (now - animation.started) / 300)
      const ease = 1 - (1 - t) ** 3
      view.x = animation.from.x + (animation.to.x - animation.from.x) * ease
      view.y = animation.from.y + (animation.to.y - animation.from.y) * ease
      view.k = animation.from.k + (animation.to.k - animation.from.k) * ease
      if (t === 1) {
        animation = null
        notifyZoom()
      }
    }
    paint()
    if (animation) requestPaint()
  }
  function finishDrag() {
    if (drag?.kind !== 'node') return
    drag.node.fx = drag.node.fy = null
    simulation.alphaTarget(0)
    // Clear the held subject before reduced-motion settling.
    drag = null
    runSimulation()
    requestPaint()
  }
  function cancelDrag() {
    finishDrag()
    drag = null
    pinch = null
    const captured = Array.from(pointers.keys())
    pointers.clear()
    for (const id of captured) if (graph.hasPointerCapture(id)) graph.releasePointerCapture(id)
    graph.classList.remove('dragging')
  }
  function reconcile() {
    const nextNodes = new Map<string, ForceNode>()
    const nextLinks = new Map<string, ForceLink>()
    let changed = false
    for (const element of scene.querySelectorAll<SVGGElement>('[data-node-key]')) {
      const key = element.dataset.nodeKey!
      const labelHalfWidth = Math.max(
        55,
        (element.querySelector<SVGTextElement>('text')?.getComputedTextLength() ?? 0) / 2 + 10
      )
      let node = nodesByKey.get(key)
      if (!node) {
        const selection: NetworkSelection =
          element.dataset.agentId !== undefined
            ? { type: 'agent', id: element.dataset.agentId }
            : { type: 'network', id: element.dataset.networkId! }
        // D3 initializes new nodes deterministically; existing nodes retain their position and velocity.
        node = { key, selection, x: NaN, y: NaN, element, labelHalfWidth }
        changed = true
      }
      node.element = element
      node.labelHalfWidth = labelHalfWidth
      nextNodes.set(key, node)
    }
    for (const element of scene.querySelectorAll<SVGLineElement>('.network-member-edge')) {
      const networkId = element.dataset.networkId!
      const agentId = element.dataset.agentId!
      const key = JSON.stringify([networkId, agentId])
      const source = nextNodes.get(`network:${networkId}`)
      const target = nextNodes.get(`agent:${agentId}`)
      if (!source || !target) continue
      let link = linksByKey.get(key)
      if (!link || link.source !== source || link.target !== target) {
        link = { source, target, element }
        changed = true
      }
      link.element = element
      nextLinks.set(key, link)
    }
    changed = changed || nodesByKey.size !== nextNodes.size || linksByKey.size !== nextLinks.size
    if (changed) cancelDrag()
    nodesByKey = nextNodes
    linksByKey = nextLinks
    if (changed) {
      nodes = Array.from(nodesByKey.values())
      links = Array.from(linksByKey.values())
      simulation.stop()
      linkForce.links([])
      simulation.nodes(nodes)
      linkForce.links(links)
      simulation.alpha(1).alphaTarget(0)
      // Fit the settled topology, not an intermediate layout that can drift offscreen.
      simulation.tick(300)
      runSimulation()
    }
    return changed
  }
  function targetView(key: string | null): View {
    const rect = stage!.getBoundingClientRect()
    let width = rect.width
    const topInset = 96
    let height = Math.max(1, rect.height - topInset - 68)
    if (details && props?.selection) {
      const panel = details.getBoundingClientRect()
      if (window.innerWidth <= 900) height = Math.min(height, panel.top - rect.top - topInset - 20)
      else width = Math.min(width, panel.left - rect.left - 20)
    }
    width = Math.max(80, width)
    height = Math.max(80, height)
    const focus = key ? nodesByKey.get(key) : undefined
    const neighborhood = focus ? new Set([focus]) : null
    if (focus && neighborhood) {
      for (const link of links) {
        if (link.source === focus) neighborhood.add(link.target)
        if (link.target === focus) neighborhood.add(link.source)
      }
    }
    let left = Infinity
    let right = -Infinity
    let top = Infinity
    let bottom = -Infinity
    for (const node of neighborhood ?? nodes) {
      left = Math.min(left, node.x - node.labelHalfWidth)
      right = Math.max(right, node.x + node.labelHalfWidth)
      top = Math.min(top, node.y - 45)
      bottom = Math.max(bottom, node.y + 45)
    }
    if (!Number.isFinite(left)) return { x: width / 2, y: topInset + height / 2, k: preferredScale ?? 1 }
    const k = preferredScale ?? Math.max(0.001, Math.min((width - 48) / (right - left), (height - 48) / (bottom - top), 1.2))
    minimumZoom = Math.min(minimumZoom, k)
    return { k, x: width / 2 - ((left + right) / 2) * k, y: topInset + height / 2 - ((top + bottom) / 2) * k }
  }
  function transitionTo(key: string | null, immediate = false) {
    cancelDrag()
    focusedKey = key && nodesByKey.has(key) ? key : null
    const target = targetView(focusedKey)
    if (immediate || motion.matches || suspended) {
      view = target
      animation = null
      notifyZoom()
    } else animation = { started: performance.now(), from: { ...view }, to: target }
    requestPaint()
  }
  function localPoint(event: MouseEvent): Point {
    const rect = graph.getBoundingClientRect()
    return { x: event.clientX - rect.left, y: event.clientY - rect.top }
  }
  function pointerDistance() {
    const [a, b] = pointers.values()
    return { distance: Math.hypot(a.x - b.x, a.y - b.y), x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 }
  }
  function onPointerDown(event: PointerEvent) {
    if (!initialized || event.button !== 0 || pointers.size >= 2) return
    animation = null
    notifyZoom()
    const p = localPoint(event)
    pointers.set(event.pointerId, p)
    graph.setPointerCapture(event.pointerId)
    if (pointers.size === 2) {
      finishDrag()
      drag = null
      const d = pointerDistance()
      pinch = { distance: d.distance, k: view.k, wx: (d.x - view.x) / view.k, wy: (d.y - view.y) / view.k }
      return
    }
    const target = event.target instanceof Element ? event.target : graph
    const element = target.closest<SVGGElement>('[data-node-key]')
    const node = element ? nodesByKey.get(element.dataset.nodeKey!) : undefined
    const start = { pointerId: event.pointerId, x: p.x, y: p.y, moved: false }
    if (node) {
      node.fx = node.x
      node.fy = node.y
      drag = { ...start, kind: 'node', node, ox: node.x, oy: node.y }
      simulation.alphaTarget(0.3)
      runSimulation()
    } else drag = { ...start, kind: 'pan', ox: view.x, oy: view.y }
    ;(element ?? graph).focus({ preventScroll: true })
    graph.classList.add('dragging')
  }
  function onPointerMove(event: PointerEvent) {
    if (!pointers.has(event.pointerId)) return
    const p = localPoint(event)
    pointers.set(event.pointerId, p)
    if (pinch && pointers.size === 2) {
      const d = pointerDistance()
      const k = Math.max(minimumZoom, Math.min(3, (pinch.k * d.distance) / Math.max(1, pinch.distance)))
      preferredScale = k
      view = { k, x: d.x - pinch.wx * k, y: d.y - pinch.wy * k }
      notifyZoom()
      requestPaint()
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
    } else {
      drag.node.fx = drag.ox + dx / view.k
      drag.node.fy = drag.oy + dy / view.k
      if (motion.matches) runSimulation()
    }
    requestPaint()
  }
  function releasePointer(event: PointerEvent) {
    if (!pointers.delete(event.pointerId)) return
    if (graph.hasPointerCapture(event.pointerId)) graph.releasePointerCapture(event.pointerId)
    if (pinch) {
      pinch = null
      const remaining = pointers.entries().next().value
      drag = remaining
        ? {
            kind: 'pan',
            pointerId: remaining[0],
            x: remaining[1].x,
            y: remaining[1].y,
            moved: true,
            ox: view.x,
            oy: view.y
          }
        : null
      if (!drag) graph.classList.remove('dragging')
      return
    }
    if (drag?.pointerId !== event.pointerId) return
    const released = drag
    finishDrag()
    drag = null
    graph.classList.remove('dragging')
    if (!released.moved && event.type === 'pointerup')
      props?.onSelect(released.kind === 'node' ? released.node.selection : null)
  }
  function activate(target: EventTarget | null) {
    if (!(target instanceof Element)) return
    const element = target.closest<SVGGElement>('[data-node-key]')
    const node = element ? nodesByKey.get(element.dataset.nodeKey!) : undefined
    props?.onSelect(node?.selection ?? null)
  }
  function zoom(factor: number, x = stage!.clientWidth / 2, y = stage!.clientHeight / 2) {
    if (!Number.isFinite(factor) || factor <= 0) return
    animation = null
    cancelDrag()
    const k = Math.max(minimumZoom, Math.min(3, view.k * factor))
    preferredScale = k
    view = { k, x: x - ((x - view.x) / view.k) * k, y: y - ((y - view.y) / view.k) * k }
    notifyZoom()
    requestPaint()
  }
  function onKeyDown(event: KeyboardEvent) {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      if (!event.repeat) activate(event.target)
      return
    }
    if (event.key === 'Escape' || event.key === '0') {
      event.preventDefault()
      preferredScale = null
      props?.onSelect(null)
      transitionTo(null)
      graph.focus({ preventScroll: true })
      return
    }
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', '+', '=', '-'].includes(event.key)) return
    event.preventDefault()
    if (event.key === '+' || event.key === '=') zoom(1.2)
    else if (event.key === '-') zoom(1 / 1.2)
    else {
      animation = null
      notifyZoom()
      cancelDrag()
      view.x += event.key === 'ArrowLeft' ? 40 : event.key === 'ArrowRight' ? -40 : 0
      view.y += event.key === 'ArrowUp' ? 40 : event.key === 'ArrowDown' ? -40 : 0
      requestPaint()
    }
  }
  function suspend() {
    suspended = true
    cancelDrag()
    simulation.stop()
    if (animation) view = animation.to
    animation = null
    cancelAnimationFrame(frame)
    frame = 0
  }
  function resume() {
    if (document.hidden || disposed) return
    suspended = false
    runSimulation()
    notifyZoom()
    requestPaint()
  }
  const { signal } = listeners
  graph.addEventListener('pointerdown', onPointerDown, { signal })
  graph.addEventListener('pointermove', onPointerMove, { signal })
  graph.addEventListener('pointerup', releasePointer, { signal })
  graph.addEventListener('pointercancel', releasePointer, { signal })
  graph.addEventListener('lostpointercapture', releasePointer, { signal })
  graph.addEventListener('keydown', onKeyDown, { signal })
  graph.addEventListener(
    'click',
    (event) => {
      if (!event.detail) activate(event.target)
    },
    { signal }
  )
  graph.addEventListener(
    'wheel',
    (event) => {
      if (!initialized) return
      event.preventDefault()
      const p = localPoint(event)
      zoom(
        Math.exp(
          -event.deltaY * (event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? stage!.clientHeight : 1) * 0.0015
        ),
        p.x,
        p.y
      )
    },
    { passive: false, signal }
  )
  motion.addEventListener(
    'change',
    () => {
      if (motion.matches && animation) {
        view = animation.to
        animation = null
        notifyZoom()
      }
      runSimulation()
      requestPaint()
    },
    { signal }
  )
  document.addEventListener('visibilitychange', () => (document.hidden ? suspend() : resume()), { signal })
  window.addEventListener('pagehide', suspend, { signal })
  window.addEventListener('pageshow', resume, { signal })
  window.addEventListener('blur', cancelDrag, { signal })
  const observer = new ResizeObserver(() => {
    if (initialized) transitionTo(focusedKey)
  })
  observer.observe(stage)

  return {
    update(next) {
      const dataChanged = props?.networks !== next.networks || props?.agents !== next.agents
      const previousSelection = props?.selection
      props = next
      const changed = dataChanged && reconcile()
      const panel = stage!.querySelector<HTMLElement>('.network-details')
      if (panel !== details) {
        if (details) observer.unobserve(details)
        details = panel
        if (details) observer.observe(details)
      }
      const selectionChanged =
        previousSelection?.type !== next.selection?.type || previousSelection?.id !== next.selection?.id
      if (!initialized || changed) transitionTo(null, !initialized)
      else if (selectionChanged) transitionTo(null)
      initialized = true
      notifyZoom()
      paint()
    },
    fitAll: () => {
      preferredScale = null
      transitionTo(null)
    },
    focusNetwork: (id) => {
      preferredScale = null
      transitionTo(`network:${id}`)
    },
    zoom: (factor) => zoom(factor),
    resetLayout() {
      preferredScale = null
      cancelDrag()
      for (const node of nodes) {
        node.x = node.y = NaN
        node.vx = node.vy = 0
      }
      simulation.stop().nodes(nodes).alpha(1).alphaTarget(0).tick(120)
      runSimulation()
      transitionTo(null)
    },
    destroy() {
      disposed = true
      listeners.abort()
      observer.disconnect()
      suspend()
      simulation.on('tick', null).on('end', null)
      nodesByKey.clear()
      linksByKey.clear()
      nodes = []
      links = []
    }
  }
}
