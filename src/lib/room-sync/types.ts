import type { MutableRefObject } from 'react'

/** Which hydration pipeline to run. */
export type HydrateRoomPhase =
  /** First load after room settings are available. */
  | 'initial'
  /** Incremental DB sync (SSE gap, manual refresh, post-send). */
  | 'reconcile'
  /** SSE reconnect: restore pending HITL only (no full message fetch). */
  | 'hitl_overlay'

export interface HydrateRoomAgentResolver {
  getAgentName: (agentId: string) => Promise<string>
  getAgentSource: (agentId: string | undefined) => 'cloud' | 'hub' | undefined
}

export interface HydrateRoomOptions extends HydrateRoomAgentResolver {
  roomId: string
  phase: HydrateRoomPhase
  getToken?: () => Promise<string | null>
  userId?: string
  userName?: string
  /** Required for `initial` stampInferredTurnTerminalStatus. */
  room?: unknown
  hitlRequestIndex?: MutableRefObject<Map<string, string>>
}

export interface HydrateRoomResult {
  /** Messages returned from API (0 if fetch failed or overlay-only). */
  rawCount: number
  /** After stale detection + hydration filter. */
  filteredCount: number
  /** Written by upsertMany (may be less than filtered due to upsert rules). */
  appliedCount: number
  pendingHitlCount: number
  fetchFailed: boolean
}
