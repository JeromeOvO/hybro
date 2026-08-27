import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook, waitFor } from '../../utils/test-utils'
import { useRoomFile } from '@/hooks/useRoomFile'
import { fetchRoomFileBlob } from '@/lib/api/files'

const getToken = vi.fn(async () => 'token')

vi.mock('@/lib/auth', () => ({
  useAuth: () => ({ userId: 'user-1', getToken }),
}))

vi.mock('@/lib/api/files', () => ({
  fetchRoomFileBlob: vi.fn(),
}))

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  vi.useRealTimers()
})

describe('useRoomFile shared blob loading', () => {
  it('deduplicates concurrent authenticated previews for the same user and file', async () => {
    const fileId = '00000000000000000000000000000001'
    vi.mocked(fetchRoomFileBlob).mockResolvedValue(new Blob(['image'], { type: 'image/png' }))
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn()
        .mockReturnValueOnce('blob:first')
        .mockReturnValueOnce('blob:second'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })

    const { result } = renderHook(() => ({
      first: useRoomFile(fileId, true),
      second: useRoomFile(fileId, true),
    }))

    await waitFor(() => {
      expect(result.current.first.objectUrl).toBe('blob:first')
      expect(result.current.second.objectUrl).toBe('blob:second')
    })
    expect(fetchRoomFileBlob).toHaveBeenCalledTimes(1)
  })

  it('does not retain a Blob that exceeds the shared byte budget', async () => {
    const fileId = '00000000000000000000000000000002'
    vi.mocked(fetchRoomFileBlob).mockResolvedValue({
      size: 65 * 1024 * 1024,
    } as Blob)
    Object.defineProperty(URL, 'createObjectURL', {
      configurable: true,
      value: vi.fn(() => 'blob:oversized'),
    })
    Object.defineProperty(URL, 'revokeObjectURL', {
      configurable: true,
      value: vi.fn(),
    })

    const first = renderHook(() => useRoomFile(fileId, true))
    await waitFor(() => expect(first.result.current.objectUrl).toBe('blob:oversized'))
    first.unmount()
    const second = renderHook(() => useRoomFile(fileId, true))
    await waitFor(() => expect(second.result.current.objectUrl).toBe('blob:oversized'))

    expect(fetchRoomFileBlob).toHaveBeenCalledTimes(2)
    second.unmount()
  })
})
