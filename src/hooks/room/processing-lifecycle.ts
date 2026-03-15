export interface ProcessingLifecycle {
  setProcessing(active: boolean): void
  setSendGuard(active: boolean): void
  isSendGuardActive(): boolean
  setMessageId(id: string | null): void
  getMessageId(): string | null
  dismissPlaceholder(): void
  resetPlaceholder(): void
  isPlaceholderDismissed(): boolean
  placeholderId(roomId: string): string
  armCancelTimeout(onTimeout: () => void, ms?: number): void
  disarmCancelTimeout(): void
  hasCancelTimedOut(): boolean
  setCancelTimedOut(v: boolean): void
  markSseDisconnection(): void
  clearSseDisconnection(): void
  hadSseDisconnection(): boolean
  reset(): void
  dispose(): void
}

export function createProcessingLifecycle(
  setZustandProcessing: (v: boolean) => void
): ProcessingLifecycle {
  let currentProcessingMessageId: string | null = null
  let placeholderDismissed = false
  let isProcessingGuard = false
  let cancelTimeout: ReturnType<typeof setTimeout> | null = null
  let cancelTimedOut = false
  let sseHadDisconnection = false

  return {
    setProcessing(active: boolean) {
      setZustandProcessing(active)
      if (!active) {
        isProcessingGuard = false
      }
    },

    setSendGuard(active: boolean) {
      isProcessingGuard = active
    },

    isSendGuardActive() {
      return isProcessingGuard
    },

    setMessageId(id: string | null) {
      currentProcessingMessageId = id
    },

    getMessageId() {
      return currentProcessingMessageId
    },

    dismissPlaceholder() {
      placeholderDismissed = true
    },

    resetPlaceholder() {
      placeholderDismissed = false
    },

    isPlaceholderDismissed() {
      return placeholderDismissed
    },

    placeholderId(roomId: string) {
      return `processing-placeholder-${roomId}`
    },

    armCancelTimeout(onTimeout: () => void, ms = 15000) {
      if (cancelTimeout) {
        clearTimeout(cancelTimeout)
      }
      cancelTimeout = setTimeout(onTimeout, ms)
    },

    disarmCancelTimeout() {
      if (cancelTimeout) {
        clearTimeout(cancelTimeout)
        cancelTimeout = null
      }
    },

    hasCancelTimedOut() {
      return cancelTimedOut
    },

    setCancelTimedOut(v: boolean) {
      cancelTimedOut = v
    },

    markSseDisconnection() {
      sseHadDisconnection = true
    },

    clearSseDisconnection() {
      sseHadDisconnection = false
    },

    hadSseDisconnection() {
      return sseHadDisconnection
    },

    reset() {
      setZustandProcessing(false)
      currentProcessingMessageId = null
      placeholderDismissed = false
      isProcessingGuard = false
      cancelTimedOut = false
      sseHadDisconnection = false
      if (cancelTimeout) {
        clearTimeout(cancelTimeout)
        cancelTimeout = null
      }
    },

    dispose() {
      if (cancelTimeout) {
        clearTimeout(cancelTimeout)
        cancelTimeout = null
      }
    },
  }
}
