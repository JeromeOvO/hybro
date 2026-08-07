import type { MutableRefObject } from 'react'
import type { ProcessingLifecycle } from '../processing-lifecycle'

export interface SSEHandlerDeps {
  roomId: string
  lifecycle: ProcessingLifecycle
  getAgentName: (agentId: string) => Promise<string>
  getAgentSource: (agentId: string | undefined) => 'cloud' | 'local' | 'hub' | undefined
  getToken?: (() => Promise<string | null>) | undefined
  reconcileWithDb: (roomId: string) => Promise<void>
  hitlRequestIndex: MutableRefObject<Map<string, string>>
  setCancelling: (v: boolean) => void
}
