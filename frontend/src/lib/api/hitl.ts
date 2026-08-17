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
  prompt_type: import('@/lib/types/sse').HITLPromptType
  choices?: string[] | null
  status: 'pending'
  expires_at?: string
  created_at: string
  interaction_id?: string | null
  interaction_status?: string | null
  application_status?: string | null
  application_error?: string | null
  group_id?: string | null
  group_total?: number | null
  group_index?: number | null
  related_message_id?: string | null
  client_request_id?: string | null
}

export interface HitlRespondResponse {
  status: string
  request_id: string
  interaction_id?: string
  client_request_id?: string | null
}

export interface HitlBatchAnswer {
  requestId: string
  answer: string
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

export async function respondToHitlBatch(
  roomId: string,
  interactionId: string,
  answers: HitlBatchAnswer[],
  clientRequestId: string | undefined,
  getToken?: () => Promise<string | null>,
): Promise<HitlRespondResponse> {
  return apiPost<HitlRespondResponse>(`${hitlUrl(roomId)}/respond-batch`, {
    interaction_id: interactionId,
    answers: answers.map(answer => ({
      request_id: answer.requestId,
      user_input: answer.answer,
    })),
    client_request_id: clientRequestId,
  }, getToken, undefined, HITL_RESPOND_TIMEOUT_MS)
}

export async function cancelHitl(
  roomId: string,
  requestId: string,
  getToken?: () => Promise<string | null>,
): Promise<{ status: string }> {
  return apiPost<{ status: string }>(`${hitlUrl(roomId)}/${requestId}/cancel`, {}, getToken)
}

export async function fetchPendingHitlRequests(
  roomId: string,
  getToken?: () => Promise<string | null>,
): Promise<HitlPendingResponse> {
  return apiGet<HitlPendingResponse>(`${hitlUrl(roomId)}/pending`, getToken)
}
