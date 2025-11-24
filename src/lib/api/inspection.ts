// Inspection-related API functions
import type { 
  InspectionCenterRequest, 
  InspectionCenterResponse,
  InsepectionCenterConnectionValidationResponse 
} from '@/lib/types'

import { getApiUrl } from '../utils'
import { getClientAuthHeaders } from '../auth'

const API_BASE_URL = getApiUrl('inspectionCenter')

// Inspect agent card
export async function inspectAgentCard(
  request: InspectionCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<InspectionCenterResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/inspectAgentCard`, {
    method: 'POST',
    headers,
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Inspect A2A connection
export async function inspectA2AConnection(
  request: InspectionCenterRequest,
  getToken?: () => Promise<string | null>
): Promise<InsepectionCenterConnectionValidationResponse> {
  const headers = await getClientAuthHeaders(getToken)
  const response = await fetch(`${API_BASE_URL}/inspectA2AConnection`, {
    method: 'POST',
    headers,
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
} 