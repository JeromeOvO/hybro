// Inspection-related API functions
import type { 
  InspectionCenterRequest, 
  InspectionCenterResponse,
  InsepectionCenterConnectionValidationResponse 
} from '@/lib/types'

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8000'

// Inspect agent card
export async function inspectAgentCard(request: InspectionCenterRequest): Promise<InspectionCenterResponse> {
  const response = await fetch(`${API_BASE_URL}/inspectionCenter/inspectAgentCard`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
}

// Inspect A2A connection
export async function inspectA2AConnection(request: InspectionCenterRequest): Promise<InsepectionCenterConnectionValidationResponse> {
  const response = await fetch(`${API_BASE_URL}/inspectionCenter/inspectA2AConnection`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(request),
  })

  if (!response.ok) {
    throw new Error(`HTTP error! status: ${response.status}`)
  }

  return await response.json()
} 