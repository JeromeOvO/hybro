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
  const displayName = user?.username || user?.firstName || 'You'
  const avatarUrl = user?.imageUrl

  if (!data.text && data.attachments.length === 0) return null

  return (
    <div className="py-3" data-testid="user-input-block">
      <div className="flex items-center gap-2 mb-2 px-1">
        {avatarUrl ? (
          <img
            src={avatarUrl}
            alt={displayName}
            className="w-7 h-7 rounded-md shrink-0 object-cover"
          />
        ) : (
          <div className="flex items-center justify-center w-7 h-7 rounded-md shrink-0 bg-primary/10 border border-primary/20 text-primary text-xs font-medium">
            {displayName.slice(0, 2).toUpperCase()}
          </div>
        )}
        <span className="font-semibold text-base text-foreground">{displayName}</span>
      </div>
      <div className="pl-10 pr-2">
        {data.text && (
          <div className="text-[15px] font-normal leading-relaxed text-foreground whitespace-pre-wrap break-words">
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
  )
})
