/**
 * Tests for room name inline editing behaviour introduced in the fix:
 *  1. After a successful updateRoomName call, refreshRoomSetting() is
 *     called so the React Query cache reflects the new name.
 *  2. The updateRoomName API is NOT called twice even when saveRoomName
 *     is triggered concurrently (blur + click race).
 *
 * Strategy: extract the saveRoomName logic into a testable hook that
 * accepts updateRoomName and refreshRoomSetting as injected dependencies,
 * mirroring what page.tsx does.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { renderHook, act } from '@testing-library/react'
import { useCallback, useRef, useState } from 'react'

// ── Testable hook that replicates page.tsx's saveRoomName logic ───────────────

type Room = { room_id: string; room_name: string }
type SaveResult = { success: boolean; error?: string }

interface UseSaveRoomNameDeps {
  updateRoomName: (id: string, name: string) => Promise<SaveResult>
  refreshRoomSetting: () => Promise<void>
}

function useSaveRoomName(
  room: Room | null,
  roomId: string,
  { updateRoomName, refreshRoomSetting }: UseSaveRoomNameDeps,
) {
  const [editingName, setEditingName] = useState(false)
  const [editNameValue, setEditNameValue] = useState('')
  const savingRef = useRef(false)

  const startEditingName = useCallback(() => {
    if (!room) return
    setEditNameValue(room.room_name)
    setEditingName(true)
  }, [room])

  const saveRoomName = useCallback(async () => {
    if (!room || !editNameValue.trim()) {
      setEditingName(false)
      return
    }
    if (editNameValue.trim() === room.room_name) {
      setEditingName(false)
      return
    }
    if (savingRef.current) return
    savingRef.current = true
    try {
      const result = await updateRoomName(roomId, editNameValue.trim())
      if (result.success) {
        await refreshRoomSetting()
      }
    } catch {
      // swallow — mirrors page.tsx toast.error path
    } finally {
      savingRef.current = false
    }
    setEditingName(false)
  }, [room, roomId, editNameValue, updateRoomName, refreshRoomSetting])

  return { editingName, editNameValue, setEditNameValue, startEditingName, saveRoomName }
}

// ── Tests ─────────────────────────────────────────────────────────────────────

const ROOM: Room = { room_id: 'room-1', room_name: 'Old Name' }

describe('room name inline editing', () => {
  let mockUpdateRoomName: ReturnType<typeof vi.fn>
  let mockRefreshRoomSetting: ReturnType<typeof vi.fn>
  let deps: UseSaveRoomNameDeps

  beforeEach(() => {
    mockUpdateRoomName = vi.fn<(id: string, name: string) => Promise<SaveResult>>().mockResolvedValue({ success: true })
    mockRefreshRoomSetting = vi.fn<() => Promise<void>>().mockResolvedValue(undefined)
    deps = { updateRoomName: mockUpdateRoomName, refreshRoomSetting: mockRefreshRoomSetting }
  })

  describe('success path', () => {
    it('calls updateRoomName with the trimmed new value', async () => {
      const { result } = renderHook(() => useSaveRoomName(ROOM, 'room-1', deps))

      await act(async () => result.current.startEditingName())
      await act(async () => result.current.setEditNameValue('  New Name  '))
      await act(async () => result.current.saveRoomName())

      expect(mockUpdateRoomName).toHaveBeenCalledOnce()
      expect(mockUpdateRoomName).toHaveBeenCalledWith('room-1', 'New Name')
    })

    it('calls refreshRoomSetting after a successful save', async () => {
      const { result } = renderHook(() => useSaveRoomName(ROOM, 'room-1', deps))

      await act(async () => result.current.startEditingName())
      await act(async () => result.current.setEditNameValue('New Name'))
      await act(async () => result.current.saveRoomName())

      expect(mockRefreshRoomSetting).toHaveBeenCalledOnce()
    })

    it('exits editing mode after a successful save', async () => {
      const { result } = renderHook(() => useSaveRoomName(ROOM, 'room-1', deps))

      await act(async () => result.current.startEditingName())
      await act(async () => result.current.setEditNameValue('New Name'))
      await act(async () => result.current.saveRoomName())

      expect(result.current.editingName).toBe(false)
    })
  })

  describe('no-op guards', () => {
    it('does NOT call the API when the value is unchanged', async () => {
      const { result } = renderHook(() => useSaveRoomName(ROOM, 'room-1', deps))

      await act(async () => result.current.startEditingName())
      // editNameValue initialised to room.room_name — no change
      await act(async () => result.current.saveRoomName())

      expect(mockUpdateRoomName).not.toHaveBeenCalled()
      expect(mockRefreshRoomSetting).not.toHaveBeenCalled()
    })

    it('does NOT call the API when the trimmed value is empty', async () => {
      const { result } = renderHook(() => useSaveRoomName(ROOM, 'room-1', deps))

      await act(async () => result.current.startEditingName())
      await act(async () => result.current.setEditNameValue('   '))
      await act(async () => result.current.saveRoomName())

      expect(mockUpdateRoomName).not.toHaveBeenCalled()
    })
  })

  describe('failure path', () => {
    it('does NOT call refreshRoomSetting when the API returns success:false', async () => {
      mockUpdateRoomName.mockResolvedValueOnce({ success: false, error: 'Forbidden' })

      const { result } = renderHook(() => useSaveRoomName(ROOM, 'room-1', deps))

      await act(async () => result.current.startEditingName())
      await act(async () => result.current.setEditNameValue('New Name'))
      await act(async () => result.current.saveRoomName())

      expect(mockUpdateRoomName).toHaveBeenCalledOnce()
      expect(mockRefreshRoomSetting).not.toHaveBeenCalled()
    })

    it('still exits editing mode when the API throws', async () => {
      mockUpdateRoomName.mockRejectedValueOnce(new Error('Network error'))

      const { result } = renderHook(() => useSaveRoomName(ROOM, 'room-1', deps))

      await act(async () => result.current.startEditingName())
      await act(async () => result.current.setEditNameValue('New Name'))
      await act(async () => result.current.saveRoomName())

      expect(result.current.editingName).toBe(false)
    })
  })

  describe('double-call guard (onBlur + onClick race)', () => {
    it('calls updateRoomName exactly once when saveRoomName fires twice concurrently', async () => {
      let resolveFirst!: (v: SaveResult) => void
      mockUpdateRoomName.mockReturnValueOnce(
        new Promise<SaveResult>((res) => { resolveFirst = res })
      )

      const { result } = renderHook(() => useSaveRoomName(ROOM, 'room-1', deps))

      await act(async () => result.current.startEditingName())
      await act(async () => result.current.setEditNameValue('New Name'))

      // Fire both "saves" before the first resolves (simulates blur then click).
      let save1!: Promise<void>
      let save2!: Promise<void>
      act(() => {
        save1 = result.current.saveRoomName()
        save2 = result.current.saveRoomName()
      })

      await act(async () => {
        resolveFirst({ success: true })
        await Promise.all([save1, save2])
      })

      expect(mockUpdateRoomName).toHaveBeenCalledOnce()
    })
  })
})
