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
      {/* Name + avatar — outside bubble, top-right */}
      <div className="flex items-center justify-end gap-2 mb-1.5 px-1">
        <span className="text-xs font-medium text-muted-foreground">{displayName}</span>
        {avatarUrl ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={avatarUrl}
            alt={displayName}
            className="w-6 h-6 rounded-full object-cover"
          />
        ) : (
          <div className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/15 text-primary text-[10px] font-semibold select-none">
            {displayName.slice(0, 2).toUpperCase()}
          </div>
        )}
      </div>

      {/* Bubble — right-aligned, same style as message-bubble */}
      <div className="flex justify-end w-full">
        <div className="max-w-[80%] rounded-xl p-4 shadow-sm bg-secondary text-secondary-foreground">
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
