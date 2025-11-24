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
import { getClientAuthHeaders } from '../auth'

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

  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/createNewRoom`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Inquiry room setting
export async function inquiryRoomSetting(
  room_id: string,
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id
  }

  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/inquiryRoomSetting`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Inquiry rooms by room owner ID
export async function inquiryRoomsByRoomOwnerId(
  room_owner_id: string,
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_owner_id
  }

  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/inquiryRoomsByRoomOwnerId`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
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

  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/updateRoomAgentSet`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
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

    const headers = await getClientAuthHeaders(getToken)
    const response = await fetch(`${API_BASE_URL}/updateRoomName`, {
      method: 'POST',
      headers,
      body: JSON.stringify(requestData),
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    return await response.json()
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

    const headers = await getClientAuthHeaders(getToken)
    const response = await fetch(`${API_BASE_URL}/updateRoomExtendInfo`, {
      method: 'POST',
      headers,
      body: JSON.stringify(requestData),
    })

    if (!response.ok) {
      throw new Error(`HTTP error! status: ${response.status}`)
    }

    return await response.json()
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

  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/createAndParseUserMessage`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('❌ API Error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  const result = await response.json()
  console.log('✅ API Response:', result)
  return result
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

 const headers = await getClientAuthHeaders(getToken)
 const response = await fetch(`${API_BASE_URL}/createAndParseUserMessageWithDebate`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('❌ API Error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  const result = await response.json()
  console.log('✅ API Response:', result)
  return result
}

// Query room messages
export async function inquiryRoomMessagesByRoomId(
  room_id: string,
  getToken?: () => Promise<string | null>
): Promise<RoomCenterRoomMessageResponse> {
  const requestData: RoomCenterRoomMessageRequest = {
    room_id
  }

  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/inquiryRoomMessagesByRoomId`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
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

 const headers = await getClientAuthHeaders(getToken)
 const response = await fetch(`${API_BASE_URL}/sendMessage`, {
    method: 'POST',
    headers,
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    const errorText = await response.text()
    console.error('❌ API Error:', response.status, errorText)
    throw new Error(`HTTP error! status: ${response.status}, message: ${errorText}`)
  }

  const result = await response.json()
  console.log('✅ API Response:', result)
  return result
}