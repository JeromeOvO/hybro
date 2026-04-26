'use client'

import React from 'react'
import { useUser } from '@clerk/nextjs'
import type { UserInputData } from '@/stores/turn-event-store/types'
import { UserAttachmentCard } from '@/components/message-bubble'
import { LinkifiedContent } from '@/components/markdown-content'

interface UserInputBlockProps {
  data: UserInputData
}

export const UserInputBlock = React.memo(function UserInputBlock({ data }: UserInputBlockProps) {
  const { user } = useUser()
  const displayName = user?.firstName || user?.username || 'You'
  const avatarUrl = user?.imageUrl

  if (!data.text && data.attachments.length === 0) return null

  return (
    <div className="py-3" data-testid="user-input-block">
      <div className="w-full">
        <div className="rounded-xl p-4 shadow-sm bg-secondary text-secondary-foreground">
          {data.text && (
            <div className="text-sm leading-relaxed whitespace-pre-wrap break-words">
              <LinkifiedContent content={data.text} />
            </div>
          )}
          {data.attachments.length > 0 && (
            <div className="flex flex-wrap gap-2 pt-2">
              {data.attachments.map((att) => (
                <UserAttachmentCard key={att.fileId} attachment={att} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  )
})
