import { afterEach, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../setup/msw-server'
import { getApiUrl } from '@/lib/utils'
import { closeSession, fetchSessionEvents, getSessionRecord, listSessions } from '@/lib/api/session'

const base = getApiUrl('sessions')

afterEach(() => server.resetHandlers())

describe('session api', () => {
  it('lists the owner sessions', async () => {
    const result = await listSessions()
    expect(result.sessions[0]).toMatchObject({ session_id: 'sess-1', state: 'waiting_on_owner' })
  })

  it('reads the owner record with the conversation id', async () => {
    const record = await getSessionRecord('sess-1')
    expect(record.conversation_id).toBe('conv-1')
    expect(record.client_label).toBe('Buyer agent')
  })

  it('escapes the session id and posts close', async () => {
    const seen: string[] = []
    server.use(http.post(`${base}/:id/close`, ({ params, request }) => {
      seen.push(`${request.method} ${String(params.id)} ${new URL(request.url).pathname}`)
      return HttpResponse.json({ session_id: params.id, status: 'closed' })
    }))
    const closed = await closeSession('a/b')
    expect(closed.status).toBe('closed')
    expect(seen[0]).toContain('/sessions/a%2Fb/close')
  })

  it('reads the whole event log past one page', async () => {
    // 2500 events: the route returns at most 1000 per request.
    const total = 2500
    const afters: number[] = []
    server.use(http.get(getApiUrl('sse/room/:roomId/events'), ({ request }) => {
      const url = new URL(request.url)
      const after = Number(url.searchParams.get('after'))
      const limit = Number(url.searchParams.get('limit'))
      afters.push(after)
      const events = Array.from({ length: Math.max(0, Math.min(limit, total - after)) }, (_, index) => ({ room_seq: after + index + 1 }))
      return HttpResponse.json({ room_seq: total, events })
    }))

    const result = await fetchSessionEvents('conv-1')

    expect(result.events).toHaveLength(total)
    expect(result.events.at(-1)).toMatchObject({ room_seq: total })
    expect(result.room_seq).toBe(total)
    expect(afters).toEqual([0, 1000, 2000])
  })
})
