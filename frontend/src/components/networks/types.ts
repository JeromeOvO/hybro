import type { AgentGroup } from '@/lib/types/agent-group'

export interface NetworkAgent {
  id: string
  name: string
  description: string
  skills: string[]
  status: 'active' | 'inactive' | 'deleted' | 'unavailable' | 'unknown'
}

export interface NetworkFormValues {
  name: string
  description: string
  agentIds: string[]
}

export type NetworkSelection = { type: 'network'; id: string } | { type: 'agent'; id: string }

export interface NetworkCanvasHandle {
  fitAll: () => void
  focusNetwork: (id: string) => void
  zoom: (factor: number) => void
  resetLayout: () => void
}

export interface NetworkCanvasProps {
  networks: AgentGroup[]
  agents: NetworkAgent[]
  selection: NetworkSelection | null
  onSelect: (selection: NetworkSelection | null) => void
  onZoomChange: (percent: number) => void
}

export interface NetworkSidebarProps {
  networks: AgentGroup[]
  selectedId: string | null
  loading: boolean
  refreshing: boolean
  error: string | null
  disabled: boolean
  onSelect: (id: string) => void
  onCreate: () => void
  onRetry: () => void
}

export interface AgentPickerProps {
  agents: NetworkAgent[]
  value: string[]
  onChange: (ids: string[]) => void
  existingIds?: readonly string[]
  loading: boolean
  error: string | null
  disabled?: boolean
  onRetry: () => void
}

export interface NetworkFormDialogProps {
  open: boolean
  network?: AgentGroup
  agents: NetworkAgent[]
  agentsLoading: boolean
  agentsError: string | null
  pending: boolean
  onOpenChange: (open: boolean) => void
  onRetryAgents: () => void
  onSave: (values: NetworkFormValues) => Promise<void>
}

export interface NetworkDetailsPanelProps {
  selection: { type: 'network'; network: AgentGroup } | { type: 'agent'; agent: NetworkAgent }
  networks: AgentGroup[]
  agents: NetworkAgent[]
  pending: boolean
  onClose: () => void
  onEdit: () => void
  onAddAgents: () => void
  onDelete: () => void
  onRemoveAgent: (networkId: string, agentId: string) => void
  onSelectNetwork: (id: string) => void
  onSelectAgent: (id: string) => void
}
