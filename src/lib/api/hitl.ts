import { apiPost, apiGet } from '../api-client'
import { getApiUrl } from '../utils'

function hitlUrl(roomId: string): string {
  return getApiUrl(`rooms/${roomId}/hitl`)
}

export interface HitlPendingRequest {
  request_id: string
  message_id: string
  source: 'agent' | 'supervisor'
  agent_id?: string
  agent_name?: string
  prompt: string
  prompt_type: 'text' | 'choice' | 'confirmation'
  choices?: string[] | null
  status: 'pending'
  expires_at?: string
  created_at: string
  group_id?: string | null
  group_total?: number | null
  group_index?: number | null
}

export interface HitlRespondResponse {
  status: string
  request_id: string
}

export interface HitlPendingResponse {
  requests: HitlPendingRequest[]
}

const HITL_RESPOND_TIMEOUT_MS = 180_000

export async function respondToHitl(
  roomId: string,
  requestId: string,
  userInput: string,
  getToken?: () => Promise<string | null>,
): Promise<HitlRespondResponse> {
  return apiPost<HitlRespondResponse>(`${hitlUrl(roomId)}/respond`, {
    request_id: requestId,
    user_input: userInput,
  }, getToken, undefined, HITL_RESPOND_TIMEOUT_MS)
}

export async function fetchPendingHitlRequests(
  roomId: string,
  getToken?: () => Promise<string | null>,
): Promise<HitlPendingResponse> {
  return apiGet<HitlPendingResponse>(`${hitlUrl(roomId)}/pending`, getToken)
}
