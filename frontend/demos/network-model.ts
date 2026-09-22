export interface DemoAgent {
  id: string
  name: string
  description: string
  skill: string
  status: 'active' | 'inactive'
}

export interface DemoNetwork {
  id: string
  name: string
  description: string
  members: string[]
  color: string
  x: number
  y: number
}

export interface CanvasSelection {
  networkId: string
  agentId: string | null
}

export interface CanvasHandle {
  fitAll: () => void
  focusNetwork: (id: string) => void
  zoom: (factor: number) => void
  resetLayout: () => void
}

export interface NetworkCanvasProps {
  networks: DemoNetwork[]
  selection: CanvasSelection | null
  showShared: boolean
  onSelect: (selection: CanvasSelection | null) => void
  onAddAgent: (networkId: string) => void
  onZoomChange: (percent: number) => void
}

export const agents: DemoAgent[] = [
  { id: 'story', name: 'Story Agent', description: '故事、笑话与叙事创作', skill: 'Storytelling', status: 'active' },
  { id: 'image', name: 'Image Generator Agent', description: '图像生成、编辑与视觉创作', skill: 'Image generation', status: 'active' },
  { id: 'travel', name: 'Travel Planner Agent', description: '行程规划与旅行建议', skill: 'Trip planning', status: 'active' },
  { id: 'weather', name: 'Weather Agent', description: '城市天气与未来天气预报', skill: 'Weather forecast', status: 'active' },
  { id: 'research', name: 'OpenQFR', description: '量化研究失败记录检索', skill: 'Research & retrieval', status: 'inactive' },
]

export const networkColors = ['#228493', '#8a6ab1', '#5b81b1', '#aa7957', '#588e73']

export const initialNetworks: DemoNetwork[] = [
  { id: 'demo-content-network', name: '内容创作', description: '从参考资料到故事，再到配图。', members: ['story', 'image', 'research'], color: networkColors[0], x: 325, y: 280 },
  { id: 'demo-travel-network', name: '旅行助手', description: '把天气和行程，放在同一个网络里。', members: ['travel', 'weather'], color: networkColors[1], x: 955, y: 295 },
  { id: 'demo-research-network', name: '研究助手', description: '检索参考记录，整理成可读的内容。', members: ['research', 'story', 'weather'], color: networkColors[2], x: 610, y: 735 },
]
