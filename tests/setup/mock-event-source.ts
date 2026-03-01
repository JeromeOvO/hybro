import { vi } from 'vitest'

export class MockEventSource {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSED = 2

  url: string
  readyState: number = MockEventSource.CONNECTING
  onopen: ((event: Event) => void) | null = null
  onmessage: ((event: MessageEvent) => void) | null = null
  onerror: ((event: Event) => void) | null = null

  private static instances: MockEventSource[] = []

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  close() {
    this.readyState = MockEventSource.CLOSED
  }

  simulateOpen() {
    this.readyState = MockEventSource.OPEN
    this.onopen?.(new Event('open'))
  }

  simulateMessage(data: unknown) {
    const event = new MessageEvent('message', {
      data: JSON.stringify(data),
    })
    this.onmessage?.(event)
  }

  simulateError() {
    this.onerror?.(new Event('error'))
  }

  static getLastInstance(): MockEventSource | undefined {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }

  static getLastInstanceOrFail(): MockEventSource {
    const instance = MockEventSource.getLastInstance()
    if (!instance) {
      throw new Error('No MockEventSource instance created. Did you call connection.connect()?')
    }
    return instance
  }

  static clearInstances() {
    MockEventSource.instances = []
  }

  static getInstanceCount(): number {
    return MockEventSource.instances.length
  }
}

export function installMockEventSource() {
  vi.stubGlobal('EventSource', MockEventSource)
}
