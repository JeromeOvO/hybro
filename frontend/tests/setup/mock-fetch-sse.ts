import { vi } from 'vitest'

export interface FetchCall {
  url: string
  options: RequestInit
}

// Module-level storage for fetch calls and stream instances
let instances: MockSSEStream[] = []
let fetchCalls: FetchCall[] = []

/**
 * Controllable mock for fetch-based SSE streams.
 * Replaces MockEventSource for testing SSEConnection after migration from EventSource to fetch().
 */
export class MockSSEStream {
  private controller: ReadableStreamDefaultController<Uint8Array> | null = null
  private stream: ReadableStream<Uint8Array>
  private encoder = new TextEncoder()

  constructor() {
    this.stream = new ReadableStream<Uint8Array>({
      start: (controller) => {
        this.controller = controller
      },
    })
    instances.push(this)
  }

  /** Enqueue a properly formatted SSE data event */
  simulateMessage(data: unknown): void {
    const text = `data: ${JSON.stringify(data)}\n\n`
    this.controller?.enqueue(this.encoder.encode(text))
  }

  /** Enqueue raw text (for testing malformed data) */
  simulateRawData(text: string): void {
    this.controller?.enqueue(this.encoder.encode(text))
  }

  /** Error the stream (simulates network error) */
  simulateError(error?: Error): void {
    this.controller?.error(error ?? new Error('Stream error'))
  }

  /** Close the stream gracefully (simulates server closing connection) */
  simulateClose(): void {
    this.controller?.close()
  }

  /** Get the Response object for this stream */
  toResponse(): Response {
    return new Response(this.stream, {
      status: 200,
      headers: { 'Content-Type': 'text/event-stream' },
    })
  }

  // --- Static helpers ---

  static getLastInstance(): MockSSEStream | undefined {
    return instances[instances.length - 1]
  }

  static getLastInstanceOrFail(): MockSSEStream {
    const instance = MockSSEStream.getLastInstance()
    if (!instance) {
      throw new Error('No MockSSEStream instance created. Did the SSEConnection call fetch()?')
    }
    return instance
  }

  static getInstanceCount(): number {
    return instances.length
  }

  static getLastFetchCall(): FetchCall | undefined {
    return fetchCalls[fetchCalls.length - 1]
  }

  static getLastFetchCallOrFail(): FetchCall {
    const call = MockSSEStream.getLastFetchCall()
    if (!call) {
      throw new Error('No fetch calls recorded')
    }
    return call
  }

  static getFetchCallCount(): number {
    return fetchCalls.length
  }

  static clearInstances(): void {
    instances = []
    fetchCalls = []
  }

  static recordFetchCall(call: FetchCall): void {
    fetchCalls.push(call)
  }
}
