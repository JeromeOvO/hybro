import type { SetupStatus } from '@/lib/api/setup'

/** What a banner says about the model provider, keyed by the error code it reports. */
export interface SetupNotice {
  code: string
  message: string
  /** True when model calls cannot work, so Room Chat disables its composer. */
  blocking: boolean
}

const MESSAGES: Record<string, string> = {
  unconfigured: 'No model provider is set up. Agents that use the Hybro model cannot answer until you add one.',
  credential_invalid: 'The model provider credential is missing or does not match the configuration. Save a new one to continue.',
  oauth_refresh_failed: 'The ChatGPT sign-in could not be renewed. Sign in again to keep using it.',
  config_invalid: 'config.json or auth.json is invalid. Hybro keeps the last valid settings until the file is fixed.',
  config_unreadable: 'config.json or auth.json cannot be read. Hybro keeps the last valid settings until the file is fixed.',
}
const FALLBACK = 'The model provider reported a problem. Open Settings to check it.'

export function setupNotice(status: SetupStatus | undefined): SetupNotice | null {
  if (!status) return null
  if (status.state !== 'configured') {
    return { code: status.state, message: MESSAGES[status.state], blocking: true }
  }
  if (!status.last_error) return null
  return { code: status.last_error, message: MESSAGES[status.last_error] ?? FALLBACK, blocking: false }
}

// Overview dismissal, per browser. Storing the dismissed code means a new error
// code shows the banner again.
const DISMISSED_KEY = 'hybro.setup-notice.dismissed'
const listeners = new Set<() => void>()

export function subscribeDismissed(listener: () => void): () => void {
  listeners.add(listener)
  window.addEventListener('storage', listener)
  return () => {
    listeners.delete(listener)
    window.removeEventListener('storage', listener)
  }
}

export function readDismissed(): string | null {
  try {
    return window.localStorage.getItem(DISMISSED_KEY)
  } catch {
    return null
  }
}

export function writeDismissed(code: string | null): void {
  try {
    if (code === null) window.localStorage.removeItem(DISMISSED_KEY)
    else window.localStorage.setItem(DISMISSED_KEY, code)
  } catch {
    // Storage can be unavailable (private mode); the banner then stays visible.
  }
  listeners.forEach(listener => listener())
}

export function isDismissed(notice: SetupNotice, dismissed: string | null): boolean {
  return dismissed === notice.code
}
