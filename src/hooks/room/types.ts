export interface UseRoomWebhookProps {
  roomId: string
  userId?: string
  userName?: string
  getToken?: () => Promise<string | null>
}
