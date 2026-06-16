import { getApiUrl } from '../utils'

const API_BASE_URL = getApiUrl('files')

export interface FileUploadResponse {
  file_id: string
  file_url: string
  mime_type: string
  file_name: string
  size_bytes: number
}

export async function uploadFile(
  file: File,
  roomId: string,
  getToken?: () => Promise<string | null>,
): Promise<FileUploadResponse> {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('room_id', roomId)

  const headers: Record<string, string> = {}
  if (getToken) {
    const token = await getToken()
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
  }

  const response = await fetch(`${API_BASE_URL}/upload`, {
    method: 'POST',
    headers,
    body: formData,
  })

  if (!response.ok) {
    const errorBody = await response.json().catch(() => ({}))
    const message = errorBody.detail || errorBody.error || `Upload failed (${response.status})`
    throw new Error(message)
  }

  return response.json()
}
