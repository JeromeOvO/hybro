'use client'

import React from 'react'
import { UserCircle } from 'lucide-react'
import type { UserInputData } from '@/stores/turn-event-store/types'
import { UserAttachmentCard } from '@/components/message-bubble'
import { LinkifiedContent } from '@/components/markdown-content'

interface UserInputBlockProps {
  data: UserInputData
}

export const UserInputBlock = React.memo(function UserInputBlock({ data }: UserInputBlockProps) {
  if (!data.text && data.attachments.length === 0) return null

  return (
    <div className="py-3" data-testid="user-input-block">
      <div className="flex items-center gap-2 mb-2 px-1">
        <div className="flex items-center justify-center w-7 h-7 rounded-md shrink-0 bg-primary/10 border border-primary/20 text-primary">
          <UserCircle className="h-4 w-4" />
        </div>
        <span className="font-semibold text-base text-foreground">You</span>
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
