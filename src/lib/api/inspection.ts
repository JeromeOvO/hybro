// Inspection-related API functions
import type { 
  InspectionCenterRequest, 
  InspectionCenterResponse,
  InsepectionCenterConnectionValidationResponse 
} from '@/lib/types'

import { getApiUrl } from '../utils'
import { apiPost } from '../api-client'

const API_BASE_URL = getApiUrl('inspectionCenter')

// Inspect agent card
export async function inspectAgentCard(
  request: InspectionCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<InspectionCenterResponse> {
  return apiPost<InspectionCenterResponse>(
    `${API_BASE_URL}/inspectAgentCard`,
    request,
    getToken
  )
}

// Inspect A2A connection
export async function inspectA2AConnection(
  request: InspectionCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<InsepectionCenterConnectionValidationResponse> {
  return apiPost<InsepectionCenterConnectionValidationResponse>(
    `${API_BASE_URL}/inspectA2AConnection`,
    request,
    getToken
  )
} 