'use client'

import { useState, useRef, useEffect, useMemo } from 'react'
import { Send } from 'lucide-react'
// import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { GroupSelector } from '@/components/group-selector'
import type { AgentGroup } from '@/lib/types/agent-group'
import { BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM } from '@/lib/types/agent-group'

interface Agent {
  id: string
  name: string
}

interface RoomChatInputProps {
  onSubmit: (message: string, targetGroup?: string) => void
  /**
   * When true, the editor itself is disabled (read-only).
   * For normal sending-state control, prefer using disableSend.
   */
  disabled?: boolean
  /**
   * When true, the Send button is disabled, but typing is still allowed.
   */
  disableSend?: boolean
  agents: Agent[]
  // Group selector props
  groups?: AgentGroup[]
  loadingGroups?: boolean
  selectedGroup?: string
  onGroupChange?: (groupId: string) => void
  roomAgentCount?: number
  onManageGroups?: () => void
  showGroupSelector?: boolean
  isOverride?: boolean
  onClearOverride?: () => void
  /**
   * External value to set in the editor (for quick start templates etc.)
   * When this changes to a non-empty value, it updates the editor content.
   */
  externalValue?: string
  /**
   * Callback when external value has been consumed (set in editor).
   * Call this to reset externalValue to empty after it's been applied.
   */
  onExternalValueConsumed?: () => void
}

export function RoomChatInput({ 
  onSubmit, 
  disabled = false, 
  disableSend = false, 
  agents,
  groups = [],
  loadingGroups = false,
  selectedGroup = BUILTIN_GROUP_ROOM_TEAM,
  onGroupChange,
  roomAgentCount = 0,
  onManageGroups,
  showGroupSelector = true,
  isOverride = false,
  onClearOverride,
  externalValue,
  onExternalValueConsumed,
}: RoomChatInputProps) {
  const [message, setMessage] = useState('') // Storage format: <@id|name>
  const [showAgentSuggestions, setShowAgentSuggestions] = useState(false)
  const [mentionQuery, setMentionQuery] = useState('')
  const [selectedAgentIndex, setSelectedAgentIndex] = useState(0)
  const editorRef = useRef<HTMLDivElement>(null)
  const suggestionsRef = useRef<HTMLDivElement>(null)

  const filteredAgents = agents.filter(agent =>
    agent.name.toLowerCase().includes(mentionQuery.toLowerCase())
  )

  // Reset selected index when filtered agents change
  useEffect(() => {
    setSelectedAgentIndex(0)
  }, [mentionQuery])

  // Scroll selected item into view
  useEffect(() => {
    if (showAgentSuggestions && suggestionsRef.current) {
      const selectedElement = suggestionsRef.current.children[selectedAgentIndex] as HTMLElement
      if (selectedElement) {
        selectedElement.scrollIntoView({
          block: 'nearest',
          behavior: 'smooth'
        })
      }
    }
  }, [selectedAgentIndex, showAgentSuggestions])

  // Convert storage format to display HTML
  const convertToDisplayHTML = (content: string) => {
    // Escape HTML to prevent XSS
    const escapeHtml = (text: string) => {
      const div = document.createElement('div')
      div.textContent = text
      return div.innerHTML
    }

    // Split by mentions and rebuild with HTML
    const parts: string[] = []
    let lastIndex = 0
    const mentionRegex = /<@([^|]+)\|([^>]+)>/g
    let match

    while ((match = mentionRegex.exec(content)) !== null) {
      // Add text before mention
      if (match.index > lastIndex) {
        parts.push(escapeHtml(content.slice(lastIndex, match.index)))
      }

      // Add mention span
      const id = match[1]
      const name = match[2]
      parts.push(
        `<span class="room-mention" data-id="${escapeHtml(id)}" data-name="${escapeHtml(name)}" contenteditable="false" style="background-color: rgba(59, 130, 246, 0.2); color: rgb(37, 99, 235); padding: 0 2px; border-radius: 3px; cursor: default; user-select: none;">@${escapeHtml(name)}</span>`
      )

      lastIndex = match.index + match[0].length
    }

    // Add remaining text
    if (lastIndex < content.length) {
      parts.push(escapeHtml(content.slice(lastIndex)))
    }

    return parts.join('')
  }

  // Handle external value changes (e.g., quick start templates)
  useEffect(() => {
    if (externalValue && externalValue !== message) {
      setMessage(externalValue)
      if (editorRef.current) {
        editorRef.current.innerHTML = convertToDisplayHTML(externalValue)
        // Set cursor at the end
        const range = document.createRange()
        const selection = window.getSelection()
        range.selectNodeContents(editorRef.current)
        range.collapse(false)
        selection?.removeAllRanges()
        selection?.addRange(range)
        editorRef.current.focus()
      }
      onExternalValueConsumed?.()
    }
  }, [externalValue])

  // Get plain text from editor (display format)
  const getEditorText = (): string => {
    if (!editorRef.current) return ''
    
    let text = ''
    const traverse = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        text += node.textContent || ''
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node as HTMLElement
        if (element.classList.contains('room-mention')) {
          text += `@${element.dataset.name || ''}`
        } else if (element.tagName === 'BR') {
          text += '\n'
        } else {
          node.childNodes.forEach(traverse)
        }
      }
    }
    
    editorRef.current.childNodes.forEach(traverse)
    return text
  }

  // Convert editor content to storage format
  const convertToStorageFormat = (): string => {
    if (!editorRef.current) return ''
    
    let storage = ''
    const traverse = (node: Node) => {
      if (node.nodeType === Node.TEXT_NODE) {
        storage += node.textContent || ''
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node as HTMLElement
        if (element.classList.contains('room-mention')) {
          const id = element.dataset.id || ''
          const name = element.dataset.name || ''
          storage += `<@${id}|${name}>`
        } else if (element.tagName === 'BR') {
          storage += '\n'
        } else {
          node.childNodes.forEach(traverse)
        }
      }
    }
    
    editorRef.current.childNodes.forEach(traverse)
    return storage
  }

  // Get cursor position in text
  const getCursorPosition = (): number => {
    const selection = window.getSelection()
    if (!selection || selection.rangeCount === 0 || !editorRef.current) return 0

    const range = selection.getRangeAt(0)
    const preCaretRange = range.cloneRange()
    preCaretRange.selectNodeContents(editorRef.current)
    preCaretRange.setEnd(range.endContainer, range.endOffset)
    
    let position = 0
    const traverse = (node: Node): boolean => {
      if (node === range.endContainer) {
        position += range.endOffset
        return true
      }

      if (node.nodeType === Node.TEXT_NODE) {
        position += node.textContent?.length || 0
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node as HTMLElement
        if (element.classList.contains('room-mention')) {
          position += `@${element.dataset.name || ''}`.length
        } else {
          for (const child of Array.from(node.childNodes)) {
            if (traverse(child)) return true
          }
        }
      }
      return false
    }

    for (const child of Array.from(editorRef.current.childNodes)) {
      if (traverse(child)) break
    }

    return position
  }

  // Set cursor position
  const setCursorPosition = (position: number) => {
    if (!editorRef.current) return

    const selection = window.getSelection()
    const range = document.createRange()
    
    let charCount = 0
    let found = false

    const traverse = (node: Node): boolean => {
      if (found) return true

      if (node.nodeType === Node.TEXT_NODE) {
        const textLength = node.textContent?.length || 0
        if (charCount + textLength >= position) {
          range.setStart(node, Math.min(position - charCount, textLength))
          range.collapse(true)
          found = true
          return true
        }
        charCount += textLength
      } else if (node.nodeType === Node.ELEMENT_NODE) {
        const element = node as HTMLElement
        if (element.classList.contains('room-mention')) {
          const mentionLength = `@${element.dataset.name || ''}`.length
          if (charCount + mentionLength >= position) {
            range.setStartAfter(element)
            range.collapse(true)
            found = true
            return true
          }
          charCount += mentionLength
        } else {
          for (const child of Array.from(node.childNodes)) {
            if (traverse(child)) return true
          }
        }
      }
      return false
    }

    for (const child of Array.from(editorRef.current.childNodes)) {
      if (traverse(child)) break
    }
    
    if (!found) {
      range.selectNodeContents(editorRef.current)
      range.collapse(false)
    }

    selection?.removeAllRanges()
    selection?.addRange(range)
  }

  // Handle input changes with debouncing to preserve cursor
  const handleInput = () => {
    if (!editorRef.current) return

    const cursorPos = getCursorPosition()
    const storageFormat = convertToStorageFormat()
    
    // Update storage format
    setMessage(storageFormat)

    // Get text for mention detection
    const text = getEditorText()
    
    // Check for @ mentions
    const beforeCursor = text.slice(0, cursorPos)
    const mentionMatch = beforeCursor.match(/@(\w*)$/)
    
    if (mentionMatch) {
      setMentionQuery(mentionMatch[1])
      setShowAgentSuggestions(true)
    } else {
      setShowAgentSuggestions(false)
      setMentionQuery('')
      setSelectedAgentIndex(0)
    }
  }

  // Insert mention at cursor
  const insertMention = (agent: Agent) => {
    if (!editorRef.current) return

    // Get current storage format
    const currentStorage = convertToStorageFormat()
    const text = getEditorText()
    const cursorPos = getCursorPosition()
    
    // Find @ position in text
    const beforeCursor = text.slice(0, cursorPos)
    const atIndex = beforeCursor.lastIndexOf('@')
    if (atIndex === -1) return

    // Build new text
    const beforeMention = text.slice(0, atIndex)
    
    // Find corresponding position in storage format
    let textPos = 0
    let storageBeforeMention = ''
    
    const storageRegex = /<@([^|]+)\|([^>]+)>/g
    let lastStorageIndex = 0
    let match
    
    while ((match = storageRegex.exec(currentStorage)) !== null) {
      // Text before this mention
      const textBeforeMention = currentStorage.slice(lastStorageIndex, match.index)
      const displayLength = textBeforeMention.length
      
      if (textPos + displayLength >= atIndex) {
        // The @ is in plain text before this mention
        storageBeforeMention = currentStorage.slice(0, lastStorageIndex) + 
          textBeforeMention.slice(0, atIndex - textPos)
        break
      }
      
      textPos += displayLength
      
      // This mention
      const mentionDisplayLength = `@${match[2]}`.length
      if (textPos + mentionDisplayLength >= atIndex) {
        // The @ is inside a mention (shouldn't happen, but handle it)
        storageBeforeMention = currentStorage.slice(0, match.index + match[0].length)
        break
      }
      
      textPos += mentionDisplayLength
      lastStorageIndex = match.index + match[0].length
    }
    
    if (!storageBeforeMention) {
      // The @ is in the remaining text
      const remainingText = currentStorage.slice(lastStorageIndex)
      const offsetInRemaining = atIndex - textPos
      storageBeforeMention = currentStorage.slice(0, lastStorageIndex) + 
        remainingText.slice(0, offsetInRemaining)
    }
    
    // Get storage format of after cursor
    let storageAfterCursor = ''
    textPos = 0
    lastStorageIndex = 0
    storageRegex.lastIndex = 0
    
    while ((match = storageRegex.exec(currentStorage)) !== null) {
      const textBeforeMention = currentStorage.slice(lastStorageIndex, match.index)
      const displayLength = textBeforeMention.length
      
      if (textPos + displayLength >= cursorPos) {
        const offsetInText = cursorPos - textPos
        storageAfterCursor = currentStorage.slice(lastStorageIndex + offsetInText)
        break
      }
      
      textPos += displayLength
      const mentionDisplayLength = `@${match[2]}`.length
      
      if (textPos + mentionDisplayLength >= cursorPos) {
        storageAfterCursor = currentStorage.slice(match.index + match[0].length)
        break
      }
      
      textPos += mentionDisplayLength
      lastStorageIndex = match.index + match[0].length
    }
    
    if (!storageAfterCursor && cursorPos < text.length) {
      const offsetInRemaining = cursorPos - textPos
      storageAfterCursor = currentStorage.slice(lastStorageIndex + offsetInRemaining)
    }
    
    // Build new storage format
    const newStorage = storageBeforeMention + `<@${agent.id}|${agent.name}> ` + storageAfterCursor
    
    setMessage(newStorage)
    
    // Update editor HTML
    editorRef.current.innerHTML = convertToDisplayHTML(newStorage)
    
    // Set cursor after the mention
    const newCursorPos = beforeMention.length + `@${agent.name} `.length
    setTimeout(() => {
      setCursorPosition(newCursorPos)
      editorRef.current?.focus()
    }, 0)
    
    setShowAgentSuggestions(false)
    setMentionQuery('')
    setSelectedAgentIndex(0)
  }

  // Update editor when message changes externally
  useEffect(() => {
    if (editorRef.current && message === '') {
      editorRef.current.innerHTML = ''
    }
  }, [message])

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (showAgentSuggestions && filteredAgents.length > 0) {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setSelectedAgentIndex(prev => 
            prev < filteredAgents.length - 1 ? prev + 1 : 0
          )
          break
        case 'ArrowUp':
          e.preventDefault()
          setSelectedAgentIndex(prev => 
            prev > 0 ? prev - 1 : filteredAgents.length - 1
          )
          break
        case 'Enter':
          e.preventDefault()
          if (filteredAgents[selectedAgentIndex]) {
            insertMention(filteredAgents[selectedAgentIndex])
          }
          break
        case 'Escape':
          e.preventDefault()
          setShowAgentSuggestions(false)
          setSelectedAgentIndex(0)
          break
        case 'Tab':
          e.preventDefault()
          if (filteredAgents[selectedAgentIndex]) {
            insertMention(filteredAgents[selectedAgentIndex])
          }
          break
        default:
          break
      }
    } else {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        // Allow typing but block submit
        if (!disableSend && !disabled) {
          handleSubmit()
        }
      }
    }
  }

  // Extract mentioned agents from the message
  const mentionedAgents = useMemo(() => {
    const mentionPattern = /<@([^|]+)\|([^>]+)>/g
    const mentions: { id: string; name: string }[] = []
    let match
    while ((match = mentionPattern.exec(message)) !== null) {
      mentions.push({ id: match[1], name: match[2] })
    }
    return mentions
  }, [message])

  const handleSubmit = () => {
    const trimmedMessage = message.trim()
    // If sending is disabled (e.g., previous message still processing), don't submit or clear
    if (!trimmedMessage || disableSend || disabled) {
      return
    }

    if (trimmedMessage) {
      // Determine target group: if mentions, use them; otherwise use selected group
      const targetGroup = mentionedAgents.length > 0 ? undefined : selectedGroup
      
      console.log('🚀 Submitting message (storage format):', trimmedMessage, 'targetGroup:', targetGroup)
      onSubmit(trimmedMessage, targetGroup)
      setMessage('')
      if (editorRef.current) {
        editorRef.current.innerHTML = ''
      }
      setShowAgentSuggestions(false)
      setSelectedAgentIndex(0)
    }
  }

  return (
    <div className="relative">
      {/* Agent suggestions dropdown */}
      {showAgentSuggestions && filteredAgents.length > 0 && (
        <div 
          ref={suggestionsRef}
          className="absolute bottom-full left-0 right-0 mb-2 bg-background/95 backdrop-blur-md border border-border/50 shadow-lg rounded-lg max-h-40 overflow-y-auto z-50"
        >
          {filteredAgents.map((agent, index) => (
            <button
              key={agent.id}
              onClick={() => insertMention(agent)}
              className={`w-full text-left px-3 py-2 text-sm transition-all duration-200 border-l-2 ${
                index === selectedAgentIndex
                  ? 'bg-primary text-primary-foreground font-semibold shadow-md border-l-primary-foreground'
                  : 'text-foreground hover:bg-muted/60 border-l-transparent'
              }`}
              onMouseEnter={() => setSelectedAgentIndex(index)}
            >
              <span className={`${
                index === selectedAgentIndex 
                  ? 'text-primary-foreground' 
                  : 'text-muted-foreground'
              }`}>
                @
              </span>
              <span className="font-medium">
                {agent.name}
              </span>
            </button>
          ))}
        </div>
      )}

      <div className="group relative flex flex-col rounded-3xl bg-background border border-border shadow-lg transition-all duration-300 hover:shadow-[0_0_20px_rgba(59,130,246,0.3)] hover:border-blue-500/50 focus-within:shadow-[0_0_25px_rgba(59,130,246,0.4)] focus-within:border-blue-500/70">
        {/* Group Selector - Top section */}
        {showGroupSelector && (
          <div className="px-4 pt-3 pb-1 border-b border-border/50">
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <span>To:</span>
              <GroupSelector
                selectedGroup={selectedGroup}
                onGroupChange={onGroupChange || (() => {})}
                groups={groups}
                loadingGroups={loadingGroups}
                roomAgentCount={roomAgentCount}
                mentionedAgents={mentionedAgents}
                onManageGroups={onManageGroups}
                disabled={disabled}
                isOverride={isOverride}
                onClearOverride={onClearOverride}
              />
            </div>
          </div>
        )}

        {/* Contenteditable div */}
        <div className="flex-1 p-4 pb-3">
          <div
            ref={editorRef}
            contentEditable={!disabled}
            onInput={handleInput}
            onKeyDown={handleKeyDown}
            className="w-full min-h-[50px] max-h-[200px] overflow-y-auto resize-none border-0 bg-transparent text-lg leading-7 text-foreground focus:outline-none empty:before:content-[attr(data-placeholder)] empty:before:text-muted-foreground"
            data-placeholder="Type a message... Use @ to mention agents"
            suppressContentEditableWarning
            style={{
              caretColor: 'var(--foreground)',
            }}
          />
        </div>

        {/* Controls */}
        <div className="flex items-center justify-between px-6 pb-4 pt-2">
          <div className="flex items-center gap-2 ml-auto">
            <Button
              onClick={handleSubmit}
              disabled={disableSend || disabled || !message.trim()}
              size="lg"
              className="h-12 w-12 rounded-full p-0"
            >
              <Send className="h-5 w-5" />
            </Button>
          </div>
        </div>
      </div>

      {/* Global styles for dark mode support */}
      <style dangerouslySetInnerHTML={{
        __html: `
          .room-mention {
            background-color: rgba(59, 130, 246, 0.2) !important;
            color: rgb(37, 99, 235) !important;
          }
          
          .dark .room-mention {
            background-color: rgba(59, 130, 246, 0.3) !important;
            color: rgb(96, 165, 250) !important;
          }
        `
      }} />
    </div>
  )
}
