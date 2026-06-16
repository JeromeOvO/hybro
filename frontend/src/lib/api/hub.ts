import { apiGet } from '../api-client'
import { getApiUrl } from '../utils'

export interface HubStatus {
  hub_id: string
  is_online: boolean
  last_connected_at: string | null
  agent_count: number
}

export interface HubStatusResponse {
  hubs: HubStatus[]
}

const HUB_API = getApiUrl('hub')

export async function getMyHubStatus(
  getToken?: () => Promise<string | null>,
): Promise<HubStatusResponse> {
  return apiGet<HubStatusResponse>(`${HUB_API}/my-status`, getToken)
}
