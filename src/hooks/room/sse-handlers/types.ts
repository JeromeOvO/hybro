import type { MutableRefObject } from 'react'
import type { ProcessingLifecycle } from '../processing-lifecycle'

export interface SSEHandlerDeps {
  roomId: string
  lifecycle: ProcessingLifecycle
  getAgentName: (agentId: string) => Promise<string>
  getAgentSource: (agentId: string | undefined) => 'cloud' | 'hub' | undefined
  getSupervisorMode: () => boolean
  reconcileWithDb: (roomId: string) => Promise<void>
  hitlRequestIndex: MutableRefObject<Map<string, string>>
  setCancelling: (v: boolean) => void
}
