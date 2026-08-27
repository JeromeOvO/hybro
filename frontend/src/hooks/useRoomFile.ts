'use client'

import { useCallback, useEffect, useState } from 'react'

import { fetchRoomFileBlob } from '@/lib/api/files'
import { useAuth } from '@/lib/auth'

const ROOM_FILE_BLOB_CACHE_ENTRY_LIMIT = 32
const ROOM_FILE_BLOB_CACHE_BYTE_LIMIT = 64 * 1024 * 1024
const ROOM_FILE_BLOB_CACHE_TTL_MS = 5 * 60 * 1000

type RoomFileBlobCacheEntry = {
  promise: Promise<Blob>
  sizeBytes: number
  expiryTimer?: ReturnType<typeof setTimeout>
}

const roomFileBlobCache = new Map<string, RoomFileBlobCacheEntry>()

function deleteCachedRoomFileBlob(
  cacheKey: string,
  expected?: RoomFileBlobCacheEntry,
): void {
  const entry = roomFileBlobCache.get(cacheKey)
  if (!entry || (expected && entry !== expected)) return
  if (entry.expiryTimer) clearTimeout(entry.expiryTimer)
  roomFileBlobCache.delete(cacheKey)
}

function scheduleRoomFileBlobExpiry(
  cacheKey: string,
  entry: RoomFileBlobCacheEntry,
): void {
  if (entry.expiryTimer) clearTimeout(entry.expiryTimer)
  entry.expiryTimer = setTimeout(() => {
    deleteCachedRoomFileBlob(cacheKey, entry)
  }, ROOM_FILE_BLOB_CACHE_TTL_MS)
}

function enforceRoomFileBlobCacheBudget(): void {
  let totalBytes = 0
  for (const entry of roomFileBlobCache.values()) totalBytes += entry.sizeBytes
  while (
    roomFileBlobCache.size > ROOM_FILE_BLOB_CACHE_ENTRY_LIMIT
    || totalBytes > ROOM_FILE_BLOB_CACHE_BYTE_LIMIT
  ) {
    const oldestKey = roomFileBlobCache.keys().next().value
    if (oldestKey == null) break
    const oldest = roomFileBlobCache.get(oldestKey)
    totalBytes -= oldest?.sizeBytes ?? 0
    deleteCachedRoomFileBlob(oldestKey, oldest)
  }
}

function fetchCachedRoomFileBlob(
  cacheKey: string,
  fileId: string,
  getToken: () => Promise<string | null>,
): Promise<Blob> {
  const cached = roomFileBlobCache.get(cacheKey)
  if (cached) {
    roomFileBlobCache.delete(cacheKey)
    roomFileBlobCache.set(cacheKey, cached)
    scheduleRoomFileBlobExpiry(cacheKey, cached)
    return cached.promise
  }

  const entry = {} as RoomFileBlobCacheEntry
  entry.sizeBytes = 0
  entry.promise = fetchRoomFileBlob(fileId, getToken)
    .then((blob) => {
      if (roomFileBlobCache.get(cacheKey) === entry) {
        entry.sizeBytes = blob.size
        if (blob.size > ROOM_FILE_BLOB_CACHE_BYTE_LIMIT) {
          deleteCachedRoomFileBlob(cacheKey, entry)
        } else {
          enforceRoomFileBlobCacheBudget()
        }
      }
      return blob
    })
    .catch((error) => {
      deleteCachedRoomFileBlob(cacheKey, entry)
      throw error
    })
  roomFileBlobCache.set(cacheKey, entry)
  scheduleRoomFileBlobExpiry(cacheKey, entry)
  enforceRoomFileBlobCacheBudget()
  return entry.promise
}

export function useRoomFile(fileId: string | undefined, preview: boolean) {
  const { getToken, userId } = useAuth()
  const cacheKey = `${userId ?? 'anonymous'}:${fileId ?? ''}`
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
    void fetchCachedRoomFileBlob(cacheKey, fileId, getToken)
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
  }, [cacheKey, fileId, getToken, preview])

  const download = useCallback(async (fileName: string) => {
    if (!fileId) throw new Error('File unavailable')
    const blob = await fetchCachedRoomFileBlob(cacheKey, fileId, getToken)
    const url = URL.createObjectURL(blob)
    try {
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = fileName
      anchor.click()
    } finally {
      URL.revokeObjectURL(url)
    }
  }, [cacheKey, fileId, getToken])

  return { objectUrl, error, download }
}
