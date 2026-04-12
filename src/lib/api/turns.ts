/**
 * API client for the turn events endpoint (Phase 1 backend).
 * Returns null if the endpoint doesn't exist yet (404) — callers
 * fall back to the legacy adapter.
 */

interface WireTurnEvent {
  event_id: string
  turn_id: string
  seq: number
  ts: number
  type: string
  [key: string]: unknown
}

interface WireTurnJournal {
  turn_id: string
  events: WireTurnEvent[]
}

export async function fetchRecentTurns(
  roomId: string,
  getToken?: () => Promise<string | null>,
  limit = 50,
): Promise<WireTurnJournal[] | null> {
  try {
    const token = getToken ? await getToken() : null
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (token) headers['Authorization'] = `Bearer ${token}`

    const res = await fetch(
      `/api/v1/rooms/${roomId}/turns/recent?limit=${limit}`,
      { headers },
    )

    if (!res.ok) {
      if (res.status === 404) return null
      console.warn(`[turns] fetchRecentTurns failed: ${res.status}`)
      return null
    }

    return (await res.json()) as WireTurnJournal[]
  } catch (err) {
    console.warn('[turns] fetchRecentTurns error:', err)
    return null
  }
}
