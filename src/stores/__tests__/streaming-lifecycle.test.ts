import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest'

// Mock requestAnimationFrame before importing streaming-buffer
const rafCallbacks: FrameRequestCallback[] = []
vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
  rafCallbacks.push(callback)
  return rafCallbacks.length
})

function flushRAF() {
  const callbacks = [...rafCallbacks]
  rafCallbacks.length = 0
  callbacks.forEach(cb => cb(performance.now()))
}

import { streamingBuffer } from '../../stores/streaming-buffer'
import { TypewriterManager } from '../../stores/typewriter'
import { useMessageStore } from '../../stores/message-store'
import type { IncomingMessage, MessageSource } from '../../stores/message-store'
import type { TaskState } from '../../lib/types/sse'

/**
 * Integration tests for the token streaming lifecycle.
 *
 * These test the same sequence of operations that handleSSEMessage performs,
 * using the real message store and streaming buffer — only the SSE transport
 * and React rendering are stubbed out.
 */
describe('Token Streaming Lifecycle', () => {
  const ROOM_ID = 'room-1'
  const MSG_ID = 'msg-abc'
  const AGENT_ID = 'agent-42'
  const AGENT_NAME = 'Test Agent'

  function upsert(msg: IncomingMessage, source: MessageSource) {
    useMessageStore.getState().upsertMessage(msg, source)
  }

  function getEntity(id: string) {
    return useMessageStore.getState().entities[id]
  }

  beforeEach(() => {
    useMessageStore.getState().setRoom(ROOM_ID)
    streamingBuffer.clear()
    flushRAF()
    rafCallbacks.length = 0
  })

  afterEach(() => {
    useMessageStore.getState().clearRoom()
  })

  // ─── Design doc lifecycle §3.2 steps 1-4 ───────────────────

  it('step 1: first agent_token creates placeholder entity + buffer entry', () => {
    // Simulate what handleSSEMessage does on first agent_token
    expect(getEntity(MSG_ID)).toBeUndefined()

    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      timestamp: new Date().toISOString(),
      agentId: AGENT_ID,
      isEphemeral: true,
    }, 'sse')
    streamingBuffer.append(MSG_ID, 'Hello')

    const entity = getEntity(MSG_ID)
    expect(entity).toBeDefined()
    expect(entity!.content).toBe('')
    expect(entity!.isEphemeral).toBe(true)
    expect(entity!.displayType).toBe('agent-bubble')
    expect(streamingBuffer.get(MSG_ID)).toBe('Hello')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(true)
  })

  it('step 2: subsequent tokens append to buffer without touching store', () => {
    // Create placeholder
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      timestamp: new Date().toISOString(),
      agentId: AGENT_ID,
      isEphemeral: true,
    }, 'sse')

    const versionAfterPlaceholder = useMessageStore.getState().version

    streamingBuffer.append(MSG_ID, 'Hello')
    streamingBuffer.append(MSG_ID, ' ')
    streamingBuffer.append(MSG_ID, 'World')

    // Store version should NOT have changed — tokens don't touch the store
    expect(useMessageStore.getState().version).toBe(versionAfterPlaceholder)
    expect(streamingBuffer.get(MSG_ID)).toBe('Hello World')
  })

  it('step 4: agent_response finalizes buffer and upserts authoritative content', () => {
    // Create placeholder + accumulate tokens
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      timestamp: new Date().toISOString(),
      agentId: AGENT_ID,
      isEphemeral: true,
    }, 'sse')
    streamingBuffer.append(MSG_ID, 'partial tokens...')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(true)

    // Simulate agent_response arrival
    streamingBuffer.finalize(MSG_ID)
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: 'Full final response from the agent.',
      senderName: AGENT_NAME,
      timestamp: new Date().toISOString(),
      agentId: AGENT_ID,
      isEphemeral: false,
    }, 'sse')

    const entity = getEntity(MSG_ID)
    expect(entity!.content).toBe('Full final response from the agent.')
    expect(entity!.isEphemeral).toBe(false)
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)
    expect(streamingBuffer.get(MSG_ID)).toBe('')
  })

  // ─── Design doc error handling §8 ──────────────────────────

  it('agent_response without prior tokens works normally (no streaming)', () => {
    // finalize on non-existent buffer is a no-op
    const content = streamingBuffer.finalize(MSG_ID)
    expect(content).toBe('')

    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: 'Direct response, no streaming.',
      senderName: AGENT_NAME,
      timestamp: new Date().toISOString(),
      agentId: AGENT_ID,
      isEphemeral: false,
    }, 'sse')

    const entity = getEntity(MSG_ID)
    expect(entity!.content).toBe('Direct response, no streaming.')
    expect(entity!.isEphemeral).toBe(false)
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)
  })

  it('late agent_token after agent_response is ignored', () => {
    // agent_response arrives first (or tokens arrive after finalization)
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: 'Authoritative content',
      senderName: AGENT_NAME,
      timestamp: new Date().toISOString(),
      agentId: AGENT_ID,
      isEphemeral: false,
    }, 'sse')

    // Simulate the guard in handleSSEMessage: check entity has content and is not ephemeral
    const entity = getEntity(MSG_ID)!
    const shouldIgnore = entity.content && !entity.isEphemeral
    expect(shouldIgnore).toBeTruthy()

    // If we DID accidentally append, verify the entity content is untouched
    // (the guard prevents this, but let's verify the store is stable)
    const versionBefore = useMessageStore.getState().version
    streamingBuffer.append(MSG_ID, 'late token')
    expect(useMessageStore.getState().version).toBe(versionBefore)
    expect(entity.content).toBe('Authoritative content')
  })

  it('SSE disconnect mid-stream promotes partial content and allows DB reconciliation', () => {
    // Simulate task_submitted -> agent_token -> disconnect sequence.
    // task_submitted creates an entity with taskStatus: working
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      taskStatus: 'working' as TaskState,
      timestamp: new Date().toISOString(),
    }, 'sse')

    // agent_token converts to streaming placeholder
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      isEphemeral: true,
      timestamp: new Date().toISOString(),
    }, 'sse')
    streamingBuffer.append(MSG_ID, 'Partial response that ')
    streamingBuffer.append(MSG_ID, 'was interrupted')

    // Simulate disconnect handler: promote with 'optimistic' source, clear taskStatus
    const store = useMessageStore.getState()
    for (const [messageId, partial] of streamingBuffer.entries()) {
      if (partial) {
        const existing = store.entities[messageId]
        store.upsertMessage({
          id: messageId,
          roomId: ROOM_ID,
          messageType: 'agent',
          content: partial,
          senderName: existing?.senderName || 'Agent',
          agentId: existing?.agentId,
          timestamp: existing?.timestamp || new Date().toISOString(),
          isEphemeral: false,
          taskStatus: null,
        }, 'optimistic')
      }
    }
    streamingBuffer.clear()

    const entity = getEntity(MSG_ID)
    expect(entity!.content).toBe('Partial response that was interrupted')
    expect(entity!.isEphemeral).toBe(false)
    expect(entity!.source).toBe('optimistic')
    expect(entity!.taskStatus).toBeUndefined()
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)

    // DB reconciliation must NOT be blocked by Rule 2 (SSE wins over DB)
    // even though the entity still has taskStatus: working from the original task.
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: 'Full content from DB reconciliation',
      senderName: AGENT_NAME,
      taskStatus: 'completed' as TaskState,
      timestamp: new Date().toISOString(),
    }, 'db')
    expect(getEntity(MSG_ID)!.content).toBe('Full content from DB reconciliation')
    expect(getEntity(MSG_ID)!.source).toBe('db')
  })

  it('multiple agents streaming simultaneously use independent buffers', () => {
    const MSG_A = 'msg-agent-a'
    const MSG_B = 'msg-agent-b'

    // Create placeholders for both
    upsert({
      id: MSG_A,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: 'Agent A',
      timestamp: new Date().toISOString(),
      agentId: 'a',
      isEphemeral: true,
    }, 'sse')
    upsert({
      id: MSG_B,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: 'Agent B',
      timestamp: new Date().toISOString(),
      agentId: 'b',
      isEphemeral: true,
    }, 'sse')

    // Stream tokens interleaved
    streamingBuffer.append(MSG_A, 'Hello from ')
    streamingBuffer.append(MSG_B, 'Greetings from ')
    streamingBuffer.append(MSG_A, 'Agent A')
    streamingBuffer.append(MSG_B, 'Agent B')

    expect(streamingBuffer.get(MSG_A)).toBe('Hello from Agent A')
    expect(streamingBuffer.get(MSG_B)).toBe('Greetings from Agent B')
    expect(streamingBuffer.isStreaming(MSG_A)).toBe(true)
    expect(streamingBuffer.isStreaming(MSG_B)).toBe(true)

    // Finalize A, B still streaming
    streamingBuffer.finalize(MSG_A)
    upsert({
      id: MSG_A,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: 'Final from Agent A',
      senderName: 'Agent A',
      timestamp: new Date().toISOString(),
      agentId: 'a',
      isEphemeral: false,
    }, 'sse')

    expect(streamingBuffer.isStreaming(MSG_A)).toBe(false)
    expect(streamingBuffer.isStreaming(MSG_B)).toBe(true)
    expect(getEntity(MSG_A)!.content).toBe('Final from Agent A')
    expect(getEntity(MSG_A)!.isEphemeral).toBe(false)
    expect(streamingBuffer.get(MSG_B)).toBe('Greetings from Agent B')
  })

  it('room switch clears all streaming buffers', () => {
    // Start streaming
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      timestamp: new Date().toISOString(),
      agentId: AGENT_ID,
      isEphemeral: true,
    }, 'sse')
    streamingBuffer.append(MSG_ID, 'in progress...')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(true)

    // Simulate room switch: clear buffer + setRoom
    streamingBuffer.clear()
    useMessageStore.getState().setRoom('room-2')

    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)
    expect(streamingBuffer.get(MSG_ID)).toBe('')
    expect(getEntity(MSG_ID)).toBeUndefined()
  })

  it('placeholder entity has correct displayType (agent-bubble, not task-status)', () => {
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      timestamp: new Date().toISOString(),
      agentId: AGENT_ID,
      isEphemeral: true,
    }, 'sse')

    // Streaming placeholders should render as agent-bubble (no taskStatus)
    const entity = getEntity(MSG_ID)
    expect(entity!.displayType).toBe('agent-bubble')
    expect(entity!.taskStatus).toBeUndefined()
  })

  // ─── Bug fixes: task_update lifecycle (not agent_response) ──

  it('task_submitted -> agent_token converts task-status to agent-bubble for streaming', () => {
    // task_submitted creates entity with taskStatus (renders as task-status card)
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      taskStatus: 'working' as TaskState,
      taskContent: 'Processing user request',
      timestamp: new Date().toISOString(),
    }, 'sse')

    expect(getEntity(MSG_ID)!.displayType).toBe('task-status')

    // First agent_token arrives — should convert to agent-bubble for streaming
    // (simulating the handleSSEMessage logic: re-upsert with isEphemeral: true)
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      timestamp: new Date().toISOString(),
      isEphemeral: true,
    }, 'sse')
    streamingBuffer.append(MSG_ID, 'Hello')

    const entity = getEntity(MSG_ID)
    expect(entity!.displayType).toBe('agent-bubble')
    expect(entity!.isEphemeral).toBe(true)
    expect(streamingBuffer.get(MSG_ID)).toBe('Hello')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(true)
  })

  it('task_update (completed) finalizes buffer and sets authoritative content', () => {
    // Start streaming
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      isEphemeral: true,
      timestamp: new Date().toISOString(),
    }, 'sse')
    streamingBuffer.append(MSG_ID, 'streaming content...')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(true)

    // Simulate terminal task_update (what the backend actually sends)
    streamingBuffer.finalize(MSG_ID)
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: 'Full final response from the agent.',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      taskStatus: 'completed' as TaskState,
      isEphemeral: false,
      timestamp: new Date().toISOString(),
    }, 'sse')

    const entity = getEntity(MSG_ID)
    expect(entity!.content).toBe('Full final response from the agent.')
    expect(entity!.isEphemeral).toBe(false)
    expect(entity!.displayType).toBe('agent-bubble')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)
  })

  it('full lifecycle: task_submitted -> agent_token(s) -> task_update(completed)', () => {
    // 1. task_submitted
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      taskStatus: 'working' as TaskState,
      timestamp: new Date().toISOString(),
    }, 'sse')
    expect(getEntity(MSG_ID)!.displayType).toBe('task-status')

    // 2. First agent_token converts to streaming agent-bubble
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      isEphemeral: true,
      timestamp: new Date().toISOString(),
    }, 'sse')
    streamingBuffer.append(MSG_ID, 'Hello ')

    expect(getEntity(MSG_ID)!.displayType).toBe('agent-bubble')
    expect(getEntity(MSG_ID)!.isEphemeral).toBe(true)

    // 3. More tokens
    streamingBuffer.append(MSG_ID, 'World')
    expect(streamingBuffer.get(MSG_ID)).toBe('Hello World')

    // 4. task_update(completed) finalizes
    streamingBuffer.finalize(MSG_ID)
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: 'Hello World — final version.',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      taskStatus: 'completed' as TaskState,
      isEphemeral: false,
      timestamp: new Date().toISOString(),
    }, 'sse')

    const entity = getEntity(MSG_ID)
    expect(entity!.content).toBe('Hello World — final version.')
    expect(entity!.displayType).toBe('agent-bubble')
    expect(entity!.isEphemeral).toBe(false)
    expect(entity!.taskStatus).toBe('completed')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)
  })
})

describe('Typewriter Lifecycle', () => {
  const ROOM_ID = 'room-1'
  const MSG_ID = 'msg-tw'
  const AGENT_NAME = 'Test Agent'
  const AGENT_ID = 'agent-42'

  function upsert(msg: IncomingMessage, source: MessageSource) {
    useMessageStore.getState().upsertMessage(msg, source)
  }

  function getEntity(id: string) {
    return useMessageStore.getState().entities[id]
  }

  let manager: TypewriterManager

  beforeEach(() => {
    manager = new TypewriterManager()
    useMessageStore.getState().setRoom(ROOM_ID)
    streamingBuffer.clear()
    flushRAF()
    rafCallbacks.length = 0
    vi.useFakeTimers()
  })

  afterEach(() => {
    vi.useRealTimers()
    useMessageStore.getState().clearRoom()
  })

  it('task_update(completed) without prior streaming triggers typewriter', () => {
    const content = 'Hello from the agent, this is a complete response.'

    // 1. task_submitted creates task-status entity
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      taskStatus: 'working' as TaskState,
      timestamp: new Date().toISOString(),
    }, 'sse')
    expect(getEntity(MSG_ID)!.displayType).toBe('task-status')

    // 2. task_update(completed) arrives — no prior streaming
    const wasStreaming = streamingBuffer.isStreaming(MSG_ID)
    expect(wasStreaming).toBe(false)

    // Create ephemeral entity for typewriter display
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      timestamp: new Date().toISOString(),
      isEphemeral: true,
    }, 'sse')
    expect(getEntity(MSG_ID)!.displayType).toBe('agent-bubble')

    // 3. Start typewriter
    manager.start(MSG_ID, content, () => {
      streamingBuffer.finalize(MSG_ID)
      upsert({
        id: MSG_ID,
        roomId: ROOM_ID,
        messageType: 'agent',
        content,
        senderName: AGENT_NAME,
        agentId: AGENT_ID,
        isEphemeral: false,
        taskStatus: 'completed' as TaskState,
        timestamp: new Date().toISOString(),
      }, 'sse')
    })

    // Buffer should be active
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(true)
    expect(manager.isActive(MSG_ID)).toBe(true)

    // Advance partially — content should be growing
    vi.advanceTimersByTime(100)
    const partial = streamingBuffer.get(MSG_ID)
    expect(partial.length).toBeGreaterThan(0)
    expect(partial.length).toBeLessThan(content.length)

    // Advance until complete
    vi.advanceTimersByTime(5000)

    // Typewriter should be done, entity should have final content
    expect(manager.isActive(MSG_ID)).toBe(false)
    const entity = getEntity(MSG_ID)
    expect(entity!.content).toBe(content)
    expect(entity!.isEphemeral).toBe(false)
    expect(entity!.displayType).toBe('agent-bubble')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)
  })

  it('room switch during typewriter finishes and finalizes content', () => {
    const content = 'A longer response that is being typewritten progressively.'

    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      isEphemeral: true,
      timestamp: new Date().toISOString(),
    }, 'sse')

    let finalized = false
    manager.start(MSG_ID, content, () => {
      finalized = true
      streamingBuffer.finalize(MSG_ID)
      upsert({
        id: MSG_ID,
        roomId: ROOM_ID,
        messageType: 'agent',
        content,
        senderName: AGENT_NAME,
        agentId: AGENT_ID,
        isEphemeral: false,
        timestamp: new Date().toISOString(),
      }, 'sse')
    })

    // Advance partially
    vi.advanceTimersByTime(50)
    expect(manager.isActive(MSG_ID)).toBe(true)

    // Simulate room switch
    manager.finishAll()
    streamingBuffer.clear()

    expect(finalized).toBe(true)
    expect(manager.isActive(MSG_ID)).toBe(false)
  })

  it('typewriter for agent_response (debate summary) works the same way', () => {
    const content = 'Coordinator summary for the debate.'

    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      isEphemeral: true,
      timestamp: new Date().toISOString(),
    }, 'sse')

    manager.start(MSG_ID, content, () => {
      streamingBuffer.finalize(MSG_ID)
      upsert({
        id: MSG_ID,
        roomId: ROOM_ID,
        messageType: 'agent',
        content,
        senderName: AGENT_NAME,
        agentId: AGENT_ID,
        isEphemeral: false,
        timestamp: new Date().toISOString(),
      }, 'sse')
    })

    vi.advanceTimersByTime(5000)

    const entity = getEntity(MSG_ID)
    expect(entity!.content).toBe(content)
    expect(entity!.isEphemeral).toBe(false)
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)
  })

  it('agent_token arriving during typewriter aborts it and real streaming takes over', () => {
    const fullContent = 'Full response that arrived via task_update.'

    // task_update(completed) arrives without prior streaming — typewriter starts
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      isEphemeral: true,
      timestamp: new Date().toISOString(),
    }, 'sse')

    let onCompleteCalled = false
    manager.start(MSG_ID, fullContent, () => {
      onCompleteCalled = true
    })

    // Advance partially so typewriter has delivered some content
    vi.advanceTimersByTime(50)
    expect(manager.isActive(MSG_ID)).toBe(true)
    const partialFromTypewriter = streamingBuffer.get(MSG_ID)
    expect(partialFromTypewriter.length).toBeGreaterThan(0)

    // Simulate agent_token arriving — aborts typewriter without calling onComplete
    manager.abort(MSG_ID)
    expect(onCompleteCalled).toBe(false)
    expect(manager.isActive(MSG_ID)).toBe(false)

    // Buffer entry should be cleared by abort — entity stays ephemeral
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)
    const entity = getEntity(MSG_ID)
    expect(entity!.isEphemeral).toBe(true)
    expect(entity!.content).toBe('')

    // Real streaming can now take over: re-create buffer, append tokens
    streamingBuffer.append(MSG_ID, 'Real ')
    streamingBuffer.append(MSG_ID, 'streamed ')
    streamingBuffer.append(MSG_ID, 'content')
    expect(streamingBuffer.get(MSG_ID)).toBe('Real streamed content')
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(true)
  })

  it('large agent_token triggers typewriter, task_update(completed) finishes it', () => {
    // Simulate task_submitted creating the entity
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      taskStatus: 'working' as TaskState,
      timestamp: new Date().toISOString(),
    }, 'sse')

    // Simulate agent_token with large content (>200 chars)
    // The handler would detect this is large and start a typewriter
    const largeContent = 'A'.repeat(500)
    const manager = new TypewriterManager()
    
    // First, convert entity to ephemeral agent-bubble (as agent_token handler does)
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: '',
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      isEphemeral: true,
      timestamp: new Date().toISOString(),
    }, 'sse')

    // Start typewriter for the large token (with no-op onComplete)
    manager.start(MSG_ID, largeContent, () => {})

    // Advance partially — typewriter has delivered some content
    vi.advanceTimersByTime(200)
    const partialContent = streamingBuffer.get(MSG_ID)
    expect(partialContent.length).toBeGreaterThan(0)
    expect(partialContent.length).toBeLessThan(largeContent.length)

    // Simulate task_update(completed) arriving while typewriter is running
    expect(manager.isActive(MSG_ID)).toBe(true)
    
    // Handler would call finish() then finalize
    manager.finish(MSG_ID)
    expect(streamingBuffer.get(MSG_ID)).toBe(largeContent)
    
    streamingBuffer.finalize(MSG_ID)
    expect(streamingBuffer.isStreaming(MSG_ID)).toBe(false)

    // Final upsert with full content
    upsert({
      id: MSG_ID,
      roomId: ROOM_ID,
      messageType: 'agent',
      content: largeContent,
      senderName: AGENT_NAME,
      agentId: AGENT_ID,
      isEphemeral: false,
      taskStatus: 'completed' as TaskState,
      timestamp: new Date().toISOString(),
    }, 'sse')

    const entity = getEntity(MSG_ID)
    expect(entity!.content).toBe(largeContent)
    expect(entity!.isEphemeral).toBe(false)
    expect(entity!.displayType).toBe('agent-bubble')
  })
})
