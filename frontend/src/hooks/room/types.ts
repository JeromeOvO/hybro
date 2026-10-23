export interface UseRoomWebhookProps {
  conversationId: string
  userId?: string
  userName?: string
  getToken?: () => Promise<string | null>
}
