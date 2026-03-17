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
  let disposed = false
  let currentProcessingMessageId: string | null = null
  let placeholderDismissed = false
  let isProcessingGuard = false
  let cancelTimeout: ReturnType<typeof setTimeout> | null = null
  let cancelTimedOut = false
  let sseHadDisconnection = false

  return {
    setProcessing(active: boolean) {
      if (disposed) return
      setZustandProcessing(active)
      if (!active) {
        isProcessingGuard = false
      }
    },

    setSendGuard(active: boolean) {
      if (disposed) return
      isProcessingGuard = active
    },

    isSendGuardActive() {
      if (disposed) return false
      return isProcessingGuard
    },

    setMessageId(id: string | null) {
      if (disposed) return
      currentProcessingMessageId = id
    },

    getMessageId() {
      if (disposed) return null
      return currentProcessingMessageId
    },

    dismissPlaceholder() {
      if (disposed) return
      placeholderDismissed = true
    },

    resetPlaceholder() {
      if (disposed) return
      placeholderDismissed = false
    },

    isPlaceholderDismissed() {
      if (disposed) return false
      return placeholderDismissed
    },

    placeholderId(roomId: string) {
      return `processing-placeholder-${roomId}`
    },

    armCancelTimeout(onTimeout: () => void, ms = 15000) {
      if (disposed) return
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
      if (disposed) return false
      return cancelTimedOut
    },

    setCancelTimedOut(v: boolean) {
      if (disposed) return
      cancelTimedOut = v
    },

    markSseDisconnection() {
      if (disposed) return
      sseHadDisconnection = true
    },

    clearSseDisconnection() {
      if (disposed) return
      sseHadDisconnection = false
    },

    hadSseDisconnection() {
      if (disposed) return false
      return sseHadDisconnection
    },

    reset() {
      if (disposed) return
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
      disposed = true
      if (cancelTimeout) {
        clearTimeout(cancelTimeout)
        cancelTimeout = null
      }
    },
  }
}
