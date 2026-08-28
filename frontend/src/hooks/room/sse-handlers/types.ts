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
  /** Gap-recovery surface (plan §4 rule 3): reconnect with ?snapshot=1.
   *  Held in a ref so useRoomSSEConnection can bind it after mount. */
  requestSnapshotRef?: MutableRefObject<(() => void) | null>
}
