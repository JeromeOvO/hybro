import { beforeEach, describe, expect, it, vi } from 'vitest'
import { renderHook } from '@testing-library/react'
import { ApiError } from '@/lib/api-client'
import { useRoomActions } from '@/hooks/room/useRoomActions'
import { useMessageStore } from '@/stores/message-store'

const mocks = vi.hoisted(() => ({
  submit: vi.fn(),
  cancel: vi.fn(),
  hydrate: vi.fn(),
}))

vi.mock('@/lib/api/hitl', () => ({
  respondToHitlBatch: mocks.submit,
  cancelHitl: mocks.cancel,
}))
vi.mock('@/lib/room-sync/hydrate-room', () => ({
  hydrateRoomFromDb: mocks.hydrate,
}))

function seedHitl(version = 1) {
  const store = useMessageStore.getState()
  store.clearRoom()
  store.setRoom('room-1')
  store.upsertMessage({
    id: 'hitl-1', roomId: 'room-1', messageType: 'agent', content: '',
    senderName: 'Agent', timestamp: '2030-01-01T00:00:00.000Z',
    hitlRequestId: 'request-1', hitlPrompt: 'Question?', hitlPromptType: 'text',
    hitlChoices: [], hitlInteractionId: 'interaction-1',
    hitlInteractionVersion: version, hitlInteractionStatus: 'open',
    hitlResolved: false, relatedMessageId: 'user-1', clientRequestId: 'client-1',
  }, 'db')
}

const successfulHydration = {
  rawCount: 0,
  filteredCount: 0,
  appliedCount: 0,
  pendingHitlCount: 0,
  fetchFailed: false,
  hitlFetchFailed: false,
}

function renderActions(
  reconcile = vi.fn().mockResolvedValue(undefined),
  requestSnapshot = vi.fn(),
) {
  const lifecycle = {
    resetPlaceholder: vi.fn(), resetProcessingResolved: vi.fn(),
    setPendingRunEventAck: vi.fn(), placeholderId: vi.fn(() => 'placeholder'),
    startProcessing: vi.fn(), getMessageId: vi.fn(), setCancelTimedOut: vi.fn(),
    markProcessingResolved: vi.fn(), stopProcessing: vi.fn(), disarmCancelTimeout: vi.fn(),
    armCancelTimeout: vi.fn(),
  }
  const hitlRequestIndex = { current: new Map<string, string>() }
  const setCancelling = vi.fn()
  return {
    reconcile,
    lifecycle,
    setCancelling,
    hook: renderHook(() => useRoomActions(
      'room-1', async () => null, lifecycle as never,
      hitlRequestIndex, reconcile, setCancelling, true, vi.fn(),
      async () => 'Agent', () => 'local', requestSnapshot,
    )),
    requestSnapshot,
  }
}

describe('useRoomActions HITL conflict recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seedHitl()
    mocks.submit.mockRejectedValue(new ApiError(409, 'Conflict'))
    mocks.cancel.mockResolvedValue({
      status: 'canceled',
      interaction_id: 'interaction-1',
      interaction_version: 2,
    })
    mocks.hydrate.mockResolvedValue(successfulHydration)
  })

  it('fails closed when the authoritative pending overlay reports fetch failure', async () => {
    mocks.hydrate.mockResolvedValue({ ...successfulHydration, hitlFetchFailed: true })
    const reconcile = vi.fn().mockResolvedValue(undefined)
    const { hook, requestSnapshot } = renderActions(reconcile)

    await expect(hook.result.current.respondToHitlBatch(
      'interaction-1', [{ requestId: 'request-1', answer: 'A' }], 'client-1',
    )).rejects.toThrow('Authoritative HITL pending refresh failed.')
    expect(reconcile).toHaveBeenCalledWith('room-1')
    expect(requestSnapshot).toHaveBeenCalledTimes(1)
    expect(mocks.hydrate).toHaveBeenCalledWith(expect.objectContaining({
      roomId: 'room-1', phase: 'hitl_overlay',
    }))

    await expect(hook.result.current.respondToHitlBatch(
      'interaction-1', [{ requestId: 'request-1', answer: 'A' }], 'client-1',
    )).rejects.toThrow('Authoritative HITL pending refresh failed.')
    expect(mocks.submit).toHaveBeenCalledTimes(2)
  })

  it('does not accept a conflict when the authoritative revision changed', async () => {
    mocks.hydrate.mockImplementation(async () => {
      useMessageStore.getState().upsertMessage({
        id: 'hitl-1', roomId: 'room-1', messageType: 'agent', content: '',
        senderName: 'Agent', timestamp: '2030-01-01T00:00:01.000Z',
        hitlRequestId: 'request-1', hitlPrompt: 'Question?', hitlPromptType: 'text',
        hitlChoices: [], hitlInteractionId: 'interaction-1',
        hitlInteractionVersion: 2, hitlInteractionStatus: 'responded',
        hitlApplicationStatus: 'applied', hitlResolved: true,
      }, 'sse')
      return successfulHydration
    })
    const { hook, requestSnapshot } = renderActions()

    await expect(hook.result.current.respondToHitlBatch(
      'interaction-1', [{ requestId: 'request-1', answer: 'A' }], 'client-1',
    )).rejects.toMatchObject({ status: 409 })
    expect(requestSnapshot).toHaveBeenCalledTimes(1)
  })

  it('still rethrows a typed conflict after a successful overlay refresh', async () => {
    mocks.hydrate.mockImplementation(async () => {
      useMessageStore.getState().upsertMessage({
        id: 'hitl-1', roomId: 'room-1', messageType: 'agent', content: '',
        senderName: 'Agent', timestamp: '2030-01-01T00:00:01.000Z',
        hitlRequestId: 'request-1', hitlPrompt: 'Question?', hitlPromptType: 'text',
        hitlChoices: [], hitlInteractionId: 'interaction-1',
        hitlInteractionVersion: 1, hitlInteractionStatus: 'responded',
        hitlApplicationStatus: 'applied', hitlResolved: true,
      }, 'sse')
      return successfulHydration
    })
    const { hook, requestSnapshot } = renderActions()

    await expect(hook.result.current.respondToHitlBatch(
      'interaction-1', [{ requestId: 'request-1', answer: 'A' }], 'client-1',
    )).rejects.toMatchObject({ status: 409 })
    expect(requestSnapshot).toHaveBeenCalledTimes(1)
    expect(useMessageStore.getState().entities['hitl-1']).toMatchObject({
      hitlResolved: true,
    })

    await expect(hook.result.current.respondToHitlBatch(
      'interaction-1', [{ requestId: 'request-1', answer: 'A' }], 'client-1',
    )).rejects.toMatchObject({ status: 409 })
    expect(mocks.submit).toHaveBeenCalledTimes(2)
  })
})

describe('useRoomActions HITL cancellation convergence', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    seedHitl()
    mocks.cancel.mockResolvedValue({
      status: 'canceled',
      interaction_id: 'interaction-1',
      interaction_version: 2,
    })
  })

  it('sets cancelling before the request and leaves terminal cleanup to authority', async () => {
    const { hook, lifecycle, setCancelling, reconcile, requestSnapshot } = renderActions()
    mocks.cancel.mockImplementation(async () => {
      expect(setCancelling).toHaveBeenCalledWith(true)
      expect(lifecycle.setCancelTimedOut).toHaveBeenCalledWith(false)
      return {
        status: 'canceled',
        interaction_id: 'interaction-1',
        interaction_version: 2,
      }
    })

    await hook.result.current.cancelHitlRequest('request-1', 'interaction-1')

    expect(mocks.cancel).toHaveBeenCalledTimes(1)
    expect(setCancelling).toHaveBeenCalledTimes(1)
    expect(reconcile).toHaveBeenCalledWith('room-1')
    expect(requestSnapshot).toHaveBeenCalledTimes(1)
    expect(lifecycle.markProcessingResolved).not.toHaveBeenCalled()
    expect(lifecycle.stopProcessing).not.toHaveBeenCalled()
    expect(useMessageStore.getState().entities['hitl-1']).toMatchObject({
      hitlResolved: true,
      hitlInteractionStatus: 'canceled',
      taskStatus: 'canceled',
    })
  })

  it('clears cancelling after a failed request and conflict reconciliation', async () => {
    mocks.cancel.mockRejectedValue(new ApiError(409, 'Conflict'))
    const { hook, setCancelling, reconcile, requestSnapshot } = renderActions()

    await expect(hook.result.current.cancelHitlRequest(
      'request-1', 'interaction-1',
    )).rejects.toMatchObject({ status: 409 })

    expect(reconcile).toHaveBeenCalledWith('room-1')
    expect(setCancelling.mock.calls).toEqual([[true], [false]])
    expect(requestSnapshot).not.toHaveBeenCalled()
  })

  it('clears cancelling after a non-conflict request failure', async () => {
    mocks.cancel.mockRejectedValue(new Error('network unavailable'))
    const { hook, setCancelling, reconcile } = renderActions()

    await expect(hook.result.current.cancelHitlRequest(
      'request-1', 'interaction-1',
    )).rejects.toThrow('network unavailable')

    expect(reconcile).not.toHaveBeenCalled()
    expect(setCancelling.mock.calls).toEqual([[true], [false]])
  })

  it('keeps accepted cancellation successful when recovery fails', async () => {
    const reconcile = vi.fn().mockRejectedValue(new Error('reconcile failed'))
    const requestSnapshot = vi.fn(() => {
      throw new Error('snapshot failed')
    })
    const consoleError = vi.spyOn(console, 'error').mockImplementation(() => undefined)
    const { hook, setCancelling } = renderActions(reconcile, requestSnapshot)

    await expect(hook.result.current.cancelHitlRequest(
      'request-1', 'interaction-1',
    )).resolves.toBeUndefined()

    expect(setCancelling.mock.calls).toEqual([[true]])
    expect(requestSnapshot).toHaveBeenCalledTimes(1)
    expect(consoleError).toHaveBeenCalledTimes(2)
    consoleError.mockRestore()
  })
})
