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

import { getApiUrl } from '../utils'
import { apiPost } from '../api-client'

const API_BASE_URL = getApiUrl('roomCenter')

// Create new room
export async function createNewRoom(
  room_name: string,
  room_owner_id: string,
  room_owner_name: string,
  getToken?: () => Promise<string | null>,
  room_agent_set?: { [k: string]: string },
  extend_info?: { [k: string]: unknown } | null
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_name,
    room_owner_id,
    room_owner_name,
    room_agent_set,
    extend_info
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
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id
  }

  return apiPost<RoomCenterRoomSettingResponse>(
    `${API_BASE_URL}/inquiryRoomSetting`,
    requestData,
    getToken
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

// Update room agent set
export async function updateRoomAgentSet(
  room_id: string,
  room_agent_set: { [k: string]: string },
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id,
    room_agent_set
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



export async function createAndParseUserMessageWithDebate(
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

  console.log('🚀 Sending createAndParseUserMessageWithDebate request:', JSON.stringify(requestData, null, 2))

  try {
    const result = await apiPost<RoomCenterUserMessageResponse>(
      `${API_BASE_URL}/createAndParseUserMessageWithDebate`,
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
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomMessageResponse> {
  const requestData: RoomCenterRoomMessageRequest = {
    room_id
  }

  return apiPost<RoomCenterRoomMessageResponse>(
    `${API_BASE_URL}/inquiryRoomMessagesByRoomId`,
    requestData,
    getToken
  )
}


export async function SendMessage(
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

  console.log('🚀 Sending SendMessage request:', JSON.stringify(requestData, null, 2))

  try {
    const result = await apiPost<RoomCenterUserMessageResponse>(
      `${API_BASE_URL}/sendMessage`,
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