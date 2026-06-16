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
    <div className="conversation-hitl-panel" data-testid="hitl-response-bar">
      <div className="conversation-hitl-panel-header">
        <MessageCircleQuestion className="h-4 w-4 conversation-hitl-panel-icon" />
        <span className="conversation-hitl-panel-title">{sourceLabel} is asking</span>
        {hitls.length > 1 && (
          <div className="conversation-hitl-panel-pager">
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
      <p className="conversation-hitl-panel-prompt">{current.prompt}</p>

      {current.promptType === 'choice' && current.choices ? (
        <div className="conversation-hitl-actions" data-testid="hitl-actions">
          {current.choices.map((choice) => (
            <Button
              key={choice}
              variant="outline"
              size="sm"
              disabled={submitting}
              onClick={() => handleSubmit(choice)}
              className="conversation-hitl-option-button"
            >
              {choice}
            </Button>
          ))}
        </div>
      ) : current.promptType === 'confirmation' ? (
        <div className="conversation-hitl-actions" data-testid="hitl-actions">
          <Button size="sm" disabled={submitting} onClick={() => handleSubmit('approved')} className="conversation-hitl-option-button">
            Approve
          </Button>
          <Button variant="outline" size="sm" disabled={submitting} onClick={() => handleSubmit('rejected')} className="conversation-hitl-option-button">
            Reject
          </Button>
        </div>
      ) : (
        <div className="conversation-hitl-text-response">
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && inputValue.trim()) handleSubmit(inputValue.trim()) }}
            placeholder="Answer question..."
            disabled={submitting}
            className="conversation-hitl-text-input"
          />
          <Button
            size="sm"
            disabled={submitting || !inputValue.trim()}
            onClick={() => handleSubmit(inputValue.trim())}
            className="conversation-hitl-submit-button"
          >
            Submit
          </Button>
        </div>
      )}
    </div>
  )
}
