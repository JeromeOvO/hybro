/** Status of a file being uploaded before message send. */
export type AttachmentStatus = 'pending' | 'uploading' | 'uploaded' | 'error'

/** Client-side representation of a file the user wants to attach. */
export interface PendingAttachment {
  id: string
  file: File
  previewUrl: string | null
  status: AttachmentStatus
  progress?: number
  error?: string
  uploaded?: AttachmentData
}

/** Server-confirmed attachment data sent with a message. */
export interface AttachmentData {
  fileId: string
  fileUrl?: string
  mimeType: string
  fileName: string
  sizeBytes: number
}
