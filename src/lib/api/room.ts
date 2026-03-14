// Room-related API functions
import type { 
  RoomCenterRoomSettingResponse, 
  RoomCenterUserMessageResponse,
  RoomCenterRoomMessageResponse
} from '@/lib/types/response'
import type {
  RoomCenterRoomSettingRequest,
  RoomCenterUserMessageRequest,
  RoomCenterRoomMessageRequest,
} from '@/lib/types/request'
import type { RoomMembershipWriteInput, MessageDispatchInput } from '@/lib/types/agent-group'

import { getApiUrl } from '../utils'
import { apiPost } from '../api-client'

const API_BASE_URL = getApiUrl('roomCenter')

export interface CreateRoomParams {
  room_name: string
  room_owner_id: string
  room_owner_name: string
  getToken?: () => Promise<string | null>
  extend_info?: { [k: string]: unknown } | null
  membership?: RoomMembershipWriteInput
  /** @deprecated Use membership instead. */
  room_agent_set?: { [k: string]: string }
  /** @deprecated Use membership.seed_group_id instead. */
  applied_from_group?: string
}

export async function createNewRoom(
  room_name: string,
  room_owner_id: string,
  room_owner_name: string,
  getToken?: () => Promise<string | null>,
  room_agent_set?: { [k: string]: string },
  extend_info?: { [k: string]: unknown } | null,
  applied_from_group?: string,
  membership?: RoomMembershipWriteInput,
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_name,
    room_owner_id,
    room_owner_name,
    extend_info,
    // Legacy fields (still sent during rollout)
    room_agent_set,
    applied_from_group,
  }

  // Overlay canonical membership fields when provided
  if (membership) {
    requestData.membership_seed_input = membership.membership_seed_input
    if ('room_agent_ids' in membership) {
      requestData.room_agent_ids = membership.room_agent_ids
    }
    if ('seed_group_id' in membership) {
      requestData.seed_group_id = membership.seed_group_id
    }
    if ('seed_all_current_agents' in membership) {
      requestData.seed_all_current_agents = membership.seed_all_current_agents
    }
  }

  return apiPost<RoomCenterRoomSettingResponse>(
    `${API_BASE_URL}/createNewRoom`,
    requestData,
    getToken
  )
}

// Inquiry room setting
export async function inquiryRoomSetting(
  room_id: string,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id
  }

  return apiPost<RoomCenterRoomSettingResponse>(
    `${API_BASE_URL}/inquiryRoomSetting`,
    requestData,
    getToken,
    signal
  )
}

// Inquiry rooms by room owner ID
export async function inquiryRoomsByRoomOwnerId(
  room_owner_id: string,
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_owner_id
  }

  return apiPost<RoomCenterRoomSettingResponse>(
    `${API_BASE_URL}/inquiryRoomsByRoomOwnerId`,
    requestData,
    getToken
  )
}

export async function updateRoomAgentSet(
  room_id: string,
  room_agent_set: { [k: string]: string },
  getToken?: () => Promise<string | null>,
  membership?: RoomMembershipWriteInput,
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id,
    room_agent_set,
  }

  if (membership) {
    requestData.membership_seed_input = membership.membership_seed_input
    if ('room_agent_ids' in membership) {
      requestData.room_agent_ids = membership.room_agent_ids
    }
    if ('seed_group_id' in membership) {
      requestData.seed_group_id = membership.seed_group_id
    }
    if ('seed_all_current_agents' in membership) {
      requestData.seed_all_current_agents = membership.seed_all_current_agents
    }
  }

  return apiPost<RoomCenterRoomSettingResponse>(
    `${API_BASE_URL}/updateRoomAgentSet`,
    requestData,
    getToken
  )
}

// Update room name
export async function updateRoomName(
  room_id: string,
  room_name: string,
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id,
    room_name
  }

  return apiPost<RoomCenterRoomSettingResponse>(
    `${API_BASE_URL}/updateRoomName`,
    requestData,
    getToken
  )
}


export async function updateRoomExtendInfo(
  room_id: string,
  extend_info: { [k: string]: unknown } | null,
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id,
    extend_info
  }

  return apiPost<RoomCenterRoomSettingResponse>(
    `${API_BASE_URL}/updateRoomExtendInfo`,
    requestData,
    getToken
  )
}

// Create and parse user message
export async function createAndParseUserMessage(
  room_id: string,
  user_input: string,
  getToken?: () => Promise<string | null>,
  user_id?: string,
  user_name?: string
): Promise<RoomCenterUserMessageResponse> {
  const requestData: RoomCenterUserMessageRequest = {
    room_id,
    user_id: user_id || "",
    user_name: user_name || "",
    user_input,
    message: {
      room_id,
      message_id: "",
      message_type: "user",
      related_message_id: null,
      message_content: {
        message_text: user_input
      },
      user_id: user_id || "",
      extend_info: null
    }
  }

  console.log('🚀 Sending createAndParseUserMessage request:', JSON.stringify(requestData, null, 2))

  try {
    const result = await apiPost<RoomCenterUserMessageResponse>(
      `${API_BASE_URL}/createAndParseUserMessage`,
      requestData,
      getToken
    )
    console.log('✅ API Response:', result)
    return result
  } catch (error) {
    console.error('❌ API Error:', error)
    throw error
  }
}

// Query room messages
export async function inquiryRoomMessagesByRoomId(
  room_id: string,
  getToken?: () => Promise<string | null>,
  signal?: AbortSignal
): Promise<RoomCenterRoomMessageResponse> {
  const requestData: RoomCenterRoomMessageRequest = {
    room_id
  }

  return apiPost<RoomCenterRoomMessageResponse>(
    `${API_BASE_URL}/inquiryRoomMessagesByRoomId`,
    requestData,
    getToken,
    signal
  )
}


export async function SendMessage(
  room_id: string,
  user_input: string,
  getToken?: () => Promise<string | null>,
  user_id?: string,
  user_name?: string,
  target_group: string = "all_agents",
  related_message_id?: string | null,
  quoted_text?: string | null,
  attachments?: Array<{ file_id: string }>,
  dispatch?: MessageDispatchInput,
  clientRequestId?: string,
): Promise<RoomCenterUserMessageResponse> {
  const requestData: Record<string, unknown> = {
    room_id,
    user_id: user_id || "",
    user_name: user_name || "",
    user_input,
    target_group,
    message: {
      room_id,
      message_id: "",
      message_type: "user",
      related_message_id: related_message_id || null,
      message_content: {
        message_text: user_input
      },
      user_id: user_id || "",
      extend_info: quoted_text ? { quoted_text } : null
    },
  }

  // Overlay canonical dispatch fields when provided.
  // mentioned_agent_ids and target_group are mutually exclusive on the wire —
  // when a canonical MentionDispatchInput is present, drop legacy target_group.
  if (dispatch) {
    if ('mentioned_agent_ids' in dispatch) {
      requestData.mentioned_agent_ids = dispatch.mentioned_agent_ids
      delete requestData.target_group
    } else {
      requestData.message_target_mode = dispatch.message_target_mode
      if ('target_group_id' in dispatch) {
        requestData.target_group_id = dispatch.target_group_id
      }
    }
  }

  if (clientRequestId) {
    requestData.client_request_id = clientRequestId
  }

  if (attachments && attachments.length > 0) {
    requestData.attachments = attachments
  }

  try {
    const result = await apiPost<RoomCenterUserMessageResponse>(
      `${API_BASE_URL}/sendMessage`,
      requestData,
      getToken
    )
    return result
  } catch (error) {
    console.error('SendMessage API Error:', error)
    throw error
  }
}

// Agent suggestion response type
export interface SuggestAgentsResponse {
  success: boolean
  routing_strategy?: "single" | "parallel" | "sequential"
  reasoning?: string
  needs_debate?: boolean
  suggested_agents?: Array<{
    agent_id: string
    name: string
    reason: string
  }>
  error?: string
  status_code?: number
}

// Suggest agents for a message (preview for "All Agents" group)
export async function suggestAgents(
  message_text: string,
  top_k: number = 3,
  getToken?: () => Promise<string | null>
): Promise<SuggestAgentsResponse> {
  return apiPost<SuggestAgentsResponse>(
    `${API_BASE_URL}/suggestAgents`,
    { message_text, top_k },
    getToken
  )
}