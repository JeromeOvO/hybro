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

// Using Next.js API routes as proxy to avoid CORS issues
const API_BASE_URL = '/api/roomCenter'

// Create new room
export async function createNewRoom(
  room_name: string,
  room_owner_id: string,
  room_owner_name: string,
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

  const response = await fetch(`${API_BASE_URL}/createNewRoom`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Inquiry room setting
export async function inquiryRoomSetting(room_id: string): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id
  }

  const response = await fetch(`${API_BASE_URL}/inquiryRoomSetting`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Inquiry rooms by room owner ID
export async function inquiryRoomsByRoomOwnerId(room_owner_id: string): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_owner_id
  }

  const response = await fetch(`${API_BASE_URL}/inquiryRoomsByRoomOwnerId`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
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
  room_agent_set: { [k: string]: string }
): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id,
    room_agent_set
  }

  const response = await fetch(`${API_BASE_URL}/updateRoomAgentSet`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Update room name
export async function updateRoomName(room_id: string, room_name: string): Promise<RoomCenterRoomSettingResponse> {
  const requestData: RoomCenterRoomSettingRequest = {
    room_id,
    room_name
  }

  const response = await fetch(`${API_BASE_URL}/updateRoomName`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
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
  user_id?: string,
  user_name?: string
): Promise<RoomCenterUserMessageResponse> {
  // 生成一个临时的message_id（可以是空字符串或UUID）
  const message_id = ""
  
  const requestData: RoomCenterUserMessageRequest = {
    room_id,
    message: {
      room_id,
      message_id,
      related_message_id: null,
      user_id: user_id || "",
      user_name: user_name || "",
      message_content: {
        message_text: user_input
      },
      extend_info: null
    }
  }

  console.log('🚀 Sending createAndParseUserMessage request:', JSON.stringify(requestData, null, 2))

  const response = await fetch(`${API_BASE_URL}/createAndParseUserMessage`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
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
export async function inquiryRoomMessagesByRoomId(room_id: string): Promise<RoomCenterRoomMessageResponse> {
  const requestData: RoomCenterRoomMessageRequest = {
    room_id
  }

  const response = await fetch(`${API_BASE_URL}/inquiryRoomMessagesByRoomId`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(requestData),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}


