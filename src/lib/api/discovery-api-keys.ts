import { apiDelete, apiGet, apiPost } from "@/lib/api-client"
import type { APIKeyCreateRequest } from "@/lib/types/request"
import type {
  APIKeyCreateResponse,
  APIKeyListResponse,
  APIKeyOperationResponse,
} from "@/lib/types/response"
import { getApiUrl } from "@/lib/utils"

const API_BASE_URL = getApiUrl("api-keys")

export async function listApiKeys(
  getToken?: () => Promise<string | null>
): Promise<APIKeyListResponse> {
  return apiGet<APIKeyListResponse>(API_BASE_URL, getToken)
}

export async function createApiKey(
  request: APIKeyCreateRequest,
  getToken?: () => Promise<string | null>
): Promise<APIKeyCreateResponse> {
  return apiPost<APIKeyCreateResponse>(API_BASE_URL, request, getToken)
}

export async function deleteApiKey(
  keyId: string,
  getToken?: () => Promise<string | null>
): Promise<APIKeyOperationResponse> {
  return apiDelete<APIKeyOperationResponse>(`${API_BASE_URL}/${keyId}`, getToken)
}
