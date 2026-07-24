'use client'

import { useCallback, useEffect, useState } from 'react'

import { fetchRoomFileBlob } from '@/lib/api/files'
import { useAuth } from '@/lib/auth'

export function useRoomFile(fileId: string | undefined, preview: boolean) {
  const { getToken } = useAuth()
  const [objectUrl, setObjectUrl] = useState<string | null>(null)
  const [error, setError] = useState<Error | null>(null)

  useEffect(() => {
    setObjectUrl(null)
    setError(null)
    if (!fileId || !preview) {
      return
    }
    const controller = new AbortController()
    let createdUrl: string | null = null
    void fetchRoomFileBlob(fileId, getToken, controller.signal)
      .then((blob) => {
        if (controller.signal.aborted) return
        createdUrl = URL.createObjectURL(blob)
        setObjectUrl(createdUrl)
        setError(null)
      })
      .catch((cause) => {
        if (!controller.signal.aborted) {
          setObjectUrl(null)
          setError(cause instanceof Error ? cause : new Error('File unavailable'))
        }
      })
    return () => {
      controller.abort()
      if (createdUrl) URL.revokeObjectURL(createdUrl)
    }
  }, [fileId, getToken, preview])

  const download = useCallback(async (fileName: string) => {
    if (!fileId) throw new Error('File unavailable')
    const blob = await fetchRoomFileBlob(fileId, getToken)
    const url = URL.createObjectURL(blob)
    try {
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = fileName
      anchor.click()
    } finally {
      URL.revokeObjectURL(url)
    }
  }, [fileId, getToken])

  return { objectUrl, error, download }
}
