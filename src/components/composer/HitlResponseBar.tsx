'use client'

import React, { useState, useCallback } from 'react'
import { MessageCircleQuestion, ChevronLeft, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/button'

export interface HitlPromptView {
  hitlId: string
  turnId: string
  ts: number
  source: 'supervisor' | 'agent'
  agentName?: string
  prompt: string
  promptType: 'text' | 'choice' | 'confirmation'
  choices?: string[]
  groupId?: string
  groupTotal?: number
  groupIndex?: number
}

interface HitlResponseBarProps {
  hitls: HitlPromptView[]
  onSubmit: (hitlId: string, answer: string) => Promise<void>
}

export function HitlResponseBar({ hitls, onSubmit }: HitlResponseBarProps) {
  const [currentIndex, setCurrentIndex] = useState(0)
  const [inputValue, setInputValue] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const current = hitls[currentIndex] ?? hitls[0]
  if (!current) return null

  const handleSubmit = useCallback(async (answer: string) => {
    setSubmitting(true)
    try {
      await onSubmit(current.hitlId, answer)
      setInputValue('')
      if (currentIndex < hitls.length - 1) {
        setCurrentIndex(i => i + 1)
      }
    } finally {
      setSubmitting(false)
    }
  }, [current.hitlId, currentIndex, hitls.length, onSubmit])

  const sourceLabel = current.source === 'agent'
    ? (current.agentName ?? 'Agent')
    : 'HYBRO AI'

  return (
    <div className="border-b border-border p-3 bg-amber-50/50 dark:bg-amber-950/20" data-testid="hitl-response-bar">
      <div className="flex items-center gap-2 mb-2">
        <MessageCircleQuestion className="h-4 w-4 text-amber-600 dark:text-amber-400" />
        <span className="text-sm font-medium">{sourceLabel} is asking:</span>
        {hitls.length > 1 && (
          <div className="flex items-center gap-1 ml-auto">
            <Button
              variant="ghost" size="icon" className="h-6 w-6"
              disabled={currentIndex === 0}
              onClick={() => setCurrentIndex(i => i - 1)}
            >
              <ChevronLeft className="h-3 w-3" />
            </Button>
            <span className="text-xs text-muted-foreground">
              {currentIndex + 1}/{hitls.length}
            </span>
            <Button
              variant="ghost" size="icon" className="h-6 w-6"
              disabled={currentIndex >= hitls.length - 1}
              onClick={() => setCurrentIndex(i => i + 1)}
            >
              <ChevronRight className="h-3 w-3" />
            </Button>
          </div>
        )}
      </div>
      <p className="text-sm mb-2">{current.prompt}</p>

      {current.promptType === 'choice' && current.choices ? (
        <div className="flex flex-wrap gap-2">
          {current.choices.map((choice) => (
            <Button
              key={choice}
              variant="outline"
              size="sm"
              disabled={submitting}
              onClick={() => handleSubmit(choice)}
            >
              {choice}
            </Button>
          ))}
        </div>
      ) : current.promptType === 'confirmation' ? (
        <div className="flex gap-2">
          <Button size="sm" disabled={submitting} onClick={() => handleSubmit('approved')}>
            Approve
          </Button>
          <Button variant="outline" size="sm" disabled={submitting} onClick={() => handleSubmit('rejected')}>
            Reject
          </Button>
        </div>
      ) : (
        <div className="flex gap-2">
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && inputValue.trim()) handleSubmit(inputValue.trim()) }}
            placeholder="Answer question..."
            disabled={submitting}
            className="flex-1 text-sm border rounded-md px-2 py-1.5 bg-background"
          />
          <Button
            size="sm"
            disabled={submitting || !inputValue.trim()}
            onClick={() => handleSubmit(inputValue.trim())}
          >
            Submit
          </Button>
        </div>
      )}
    </div>
  )
}
