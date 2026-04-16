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
      {/* Avatar + name — outside the border, top-right */}
      <div className="flex items-center gap-2 mb-1.5 justify-end px-1">
        <span className="font-semibold text-[13px] text-foreground">{displayName}</span>
        {avatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={avatarUrl}
            alt={displayName}
            className="w-7 h-7 rounded-full object-cover"
          />
        ) : (
          <div className="flex items-center justify-center w-7 h-7 rounded-full bg-primary/15 text-primary text-xs font-semibold select-none">
            {displayName.slice(0, 2).toUpperCase()}
          </div>
        )}
      </div>

      {/* Message block — bordered, background, text left-aligned */}
      <div className="px-4 sm:px-5 py-3 bg-secondary/60 dark:bg-secondary/40 border border-border/30 rounded-md">
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
