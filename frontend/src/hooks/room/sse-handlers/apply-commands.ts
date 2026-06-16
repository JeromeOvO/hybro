import { useMessageStore } from '@/stores/message-store'
import { useStreamingStore } from '@/stores/streaming-store'
import type { IncomingMessage, MessageSource } from '@/stores/message-store/types'

export type RoomCommand =
  | { type: 'upsert_message'; message: IncomingMessage; source: MessageSource }
  | { type: 'remove_message'; id: string }
  | { type: 'cancel_all_non_terminal'; roomId: string }
  | { type: 'stream_clear'; messageId: string }
  | { type: 'stream_clear_client_request'; clientRequestId: string }

/**
 * Apply store mutations synchronously in order.
 * Used by task_update terminal path to preserve buffer read+clear invariant.
 */
export function applyRoomCommands(commands: readonly RoomCommand[]): void {
  if (commands.length === 0) return

  const store = useMessageStore.getState()
  const streaming = useStreamingStore.getState()

  for (const cmd of commands) {
    switch (cmd.type) {
      case 'upsert_message':
        store.upsertMessage(cmd.message, cmd.source)
        break
      case 'remove_message':
        store.removeMessage(cmd.id)
        break
      case 'cancel_all_non_terminal':
        store.cancelAllNonTerminal(cmd.roomId)
        break
      case 'stream_clear':
        streaming.clear(cmd.messageId)
        break
      case 'stream_clear_client_request':
        streaming.clearByClientRequestId(cmd.clientRequestId)
        break
    }
  }
}
