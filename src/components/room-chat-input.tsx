'use client'

import { useState, useRef, useEffect, useMemo } from 'react'
import Image from 'next/image'
import { ArrowUp, Square, AtSign, Maximize2, Minimize2, X, Quote, ShipWheel, Swords, ChevronsUpDown } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { GroupSelector } from '@/components/group-selector'
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip'
import type { AgentGroup } from '@/lib/types/agent-group'
import { BUILTIN_GROUP_ALL_AGENTS, BUILTIN_GROUP_ROOM_TEAM } from '@/lib/types/agent-group'
import { cn } from '@/lib/utils'
import type { QuoteData } from './message-bubble'
import type { PendingAttachment } from '@/lib/types/attachments'
import { FileAttachmentButton, ACCEPTED_MIME_SET, MAX_FILE_SIZE, MAX_ATTACHMENTS } from './file-attachment-button'
import { AttachmentPreview } from './attachment-preview'
import { getAgentAvatarUri } from '@/lib/agent-avatar'

const _parsed = parseInt(process.env.NEXT_PUBLIC_MAX_MESSAGE_LENGTH || '10000', 10)
export const MAX_MESSAGE_LENGTH = Number.isNaN(_parsed) || _parsed < 1 ? 10000 : _parsed
const COUNTER_VISIBLE_THRESHOLD = Math.floor(MAX_MESSAGE_LENGTH * 0.95)
const WARNING_THRESHOLD = Math.floor(MAX_MESSAGE_LENGTH * 0.99)

interface Agent {
  id: string
  name: string
  iconUrl?: string | null
}

interface RoomChatInputProps {
  onSubmit: (message: string, targetGroup?: string, quote?: QuoteData | null, attachments?: PendingAttachment[]) => void
  /**
   * When true, the editor itself is disabled (read-only).
   * For normal sending-state control, prefer using disableSend.
   */
  disabled?: boolean
  /**
   * When true, the Send button is disabled, but typing is still allowed.
   */
  disableSend?: boolean
  /**
   * When true, shows spinner (message is being created/parsed, cancellation won't work)
   */
  sending?: boolean
  /**
   * When true, shows Stop button instead of Send button and allows cancelling
   */
  processing?: boolean
  /**
   * When true, shows a disabled spinner button indicating cancellation is in progress
   */
  cancelling?: boolean
  /**
   * Callback when user clicks Stop button to cancel ongoing processing
   */
  onCancel?: () => void
  agents: Agent[]
  /** Agent IDs belonging to the current room (for filtering mentions when room_team is selected). */
  roomAgentIds?: string[]
  // Group selector props
  groups?: AgentGroup[]
  loadingGroups?: boolean
  selectedGroup?: string
  onGroupChange?: (groupId: string) => void
  roomAgentCount?: number
  onCreateGroup?: () => void
  onEditGroup?: (group: AgentGroup) => void
  onDeleteGroup?: (group: AgentGroup) => void
  onEditRoomAgents?: () => void
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
  /** Currently quoted message (shown as preview above editor). */
  quote?: QuoteData | null
  /** Callback to clear the current quote. */
  onClearQuote?: () => void
  /**
   * Content above the editor (e.g. HITL Questions panel).
   */
  topSlot?: React.ReactNode
  /** Current supervisor mode state. */
  supervisorMode?: boolean
  /** Callback when the user toggles supervisor mode from the + menu. */
  onSupervisorChange?: (enabled: boolean) => void
  /** Current debate mode state. */
  debateMode?: boolean
  /** Callback when the user toggles debate mode from the + menu. */
  onDebateModeChange?: (enabled: boolean) => void
}

export function RoomChatInput({
  onSubmit,
  disabled = false,
  disableSend = false,
  sending = false,
  processing = false,
  cancelling = false,
  onCancel,
  agents,
  roomAgentIds = [],
  groups = [],
  loadingGroups = false,
  selectedGroup = BUILTIN_GROUP_ALL_AGENTS,
  onGroupChange,
  roomAgentCount = 0,
  onCreateGroup,
  onEditGroup,
  onDeleteGroup,
  onEditRoomAgents,
  showGroupSelector = true,
  isOverride = false,
  onClearOverride,
  externalValue,
  onExternalValueConsumed,
  quote,
  onClearQuote,
  topSlot,
  supervisorMode,
  onSupervisorChange,
  debateMode,
  onDebateModeChange,
}: RoomChatInputProps) {
  const [message, setMessage] = useState('') // Storage format: <@id|name>
  const [showAgentSuggestions, setShowAgentSuggestions] = useState(false)
  const [mentionQuery, setMentionQuery] = useState('')
  const [selectedAgentIndex, setSelectedAgentIndex] = useState(0)
  const [isEditorExpanded, setIsEditorExpanded] = useState(false)
  const [isOverflowing, setIsOverflowing] = useState(false)
  const [attachments, setAttachments] = useState<PendingAttachment[]>([])
  const [plainTextLength, setPlainTextLength] = useState(0)
  const attachmentsRef = useRef<PendingAttachment[]>([])
  const editorRef = useRef<HTMLDivElement>(null)
  const suggestionsRef = useRef<HTMLDivElement>(null)
  const listRef = useRef<HTMLDivElement>(null)

  const scopedAgents = useMemo(() => {
    if (selectedGroup === BUILTIN_GROUP_ALL_AGENTS) return agents
    if (selectedGroup === BUILTIN_GROUP_ROOM_TEAM) {
      if (roomAgentIds.length === 0) return agents
      const idSet = new Set(roomAgentIds)
      return agents.filter(a => idSet.has(a.id))
    }
    const group = groups.find(g => g.group_id === selectedGroup)
    if (!group) return agents
    const idSet = new Set(group.agents)
    return agents.filter(a => idSet.has(a.id))
  }, [agents, selectedGroup, groups, roomAgentIds])

  const filteredAgents = scopedAgents.filter(agent =>
    agent.name.toLowerCase().includes(mentionQuery.toLowerCase())
  )

  const agentNameMap = useMemo(
    () => Object.fromEntries(agents.map(a => [a.id, a.name])),
    [agents]
  )

  const addFiles = (files: File[]) => {
    const valid = files.filter(f => f.size <= MAX_FILE_SIZE && ACCEPTED_MIME_SET.has(f.type))
    if (valid.length === 0) return
    setAttachments(prev => {
      const remaining = MAX_ATTACHMENTS - prev.length
      if (remaining <= 0) {
        toast.warning(`Maximum ${MAX_ATTACHMENTS} attachments allowed`)
        return prev
      }
      const toAdd = valid.slice(0, remaining)
      if (toAdd.length < valid.length) {
        toast.warning(`Only ${remaining} more attachment${remaining === 1 ? '' : 's'} allowed — ${valid.length - toAdd.length} skipped`)
      }
      const pending: PendingAttachment[] = toAdd.map(file => {
        const needsPreview = file.type.startsWith('image/') || file.type.startsWith('audio/') || file.type.startsWith('video/')
        return {
          id: `att-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
          file,
          previewUrl: needsPreview ? URL.createObjectURL(file) : null,
          status: 'pending' as const,
        }
      })
      const next = [...prev, ...pending]
      attachmentsRef.current = next
      return next
    })
  }

  const removeAttachment = (id: string) => {
    setAttachments(prev => {
      const target = prev.find(a => a.id === id)
      if (target?.previewUrl) URL.revokeObjectURL(target.previewUrl)
      const next = prev.filter(a => a.id !== id)
      attachmentsRef.current = next
      return next
    })
  }

  useEffect(() => {
    return () => {
      attachmentsRef.current.forEach(a => { if (a.previewUrl) URL.revokeObjectURL(a.previewUrl) })
    }
  }, [])

  // Reset selected index when filtered agents change
  useEffect(() => {
    setSelectedAgentIndex(0)
  }, [mentionQuery])

  // Scroll selected item into view
  useEffect(() => {
    if (showAgentSuggestions && listRef.current) {
      const selectedElement = listRef.current.children[selectedAgentIndex] as HTMLElement
      if (selectedElement) {
        selectedElement.scrollIntoView({
          block: 'nearest',
          behavior: 'smooth'
        })
      }
    }
  }, [selectedAgentIndex, showAgentSuggestions])

  // Close mention dropdown on outside click
  useEffect(() => {
    if (!showAgentSuggestions) return
    const handleMouseDown = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        suggestionsRef.current?.contains(target) ||
        editorRef.current?.contains(target)
      ) return
      setShowAgentSuggestions(false)
      setSelectedAgentIndex(0)
    }
    document.addEventListener('mousedown', handleMouseDown)
    return () => document.removeEventListener('mousedown', handleMouseDown)
  }, [showAgentSuggestions])

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

      // Add mention span using room-mention class (styles defined in globals.css)
      const id = match[1]
      const name = match[2]
      parts.push(
        `<span class="room-mention" data-id="${escapeHtml(id)}" data-name="${escapeHtml(name)}" contenteditable="false">@${escapeHtml(name)}</span>`
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
      const displayLength = externalValue.replace(/<@[^|]+\|([^>]+)>/g, '@$1').length
      setPlainTextLength(displayLength)
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
  }, [externalValue, message, onExternalValueConsumed])

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
    setPlainTextLength(text.length)

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

    // Detect if editor content overflows the visible area
    if (editorRef.current) {
      setIsOverflowing(editorRef.current.scrollHeight > editorRef.current.clientHeight)
    }
  }

  // Paste plain text preserving newlines and whitespace
  const handlePaste = (e: React.ClipboardEvent<HTMLDivElement>) => {
    const items = Array.from(e.clipboardData.items)
    const fileItems = items
      .filter(item => item.kind === 'file')
      .map(item => item.getAsFile())
      .filter((f): f is File => f !== null)

    if (fileItems.length > 0) {
      e.preventDefault()
      addFiles(fileItems)
      return
    }

    e.preventDefault()
    const text = e.clipboardData.getData('text/plain')
    if (!editorRef.current) return

    // Enforce message size limit on paste
    const currentText = getEditorText()
    const selectionLength = window.getSelection()?.toString().length ?? 0
    const availableChars = MAX_MESSAGE_LENGTH - (currentText.length - selectionLength)

    if (availableChars <= 0) {
      toast.warning(`Message is at the ${MAX_MESSAGE_LENGTH.toLocaleString()} character limit`)
      return
    }

    let textToInsert = text
    if (text.length > availableChars) {
      textToInsert = text.slice(0, availableChars)
      const truncated = text.length - availableChars
      toast.warning(`Pasted text truncated: ${truncated.toLocaleString()} characters removed to stay within the ${MAX_MESSAGE_LENGTH.toLocaleString()} character limit`)
    }

    const selection = window.getSelection()
    if (!selection || selection.rangeCount === 0) return

    // Delete any selected content
    selection.deleteFromDocument()

    const range = selection.getRangeAt(0)

    // Split by newlines and insert text nodes with <br> elements between them
    const lines = textToInsert.split('\n')
    let lastNode: Node | null = null

    lines.forEach((line, index) => {
      if (index > 0) {
        // Insert a <br> for each newline
        const br = document.createElement('br')
        range.insertNode(br)
        range.setStartAfter(br)
        range.collapse(true)
        lastNode = br
      }
      if (line.length > 0) {
        const textNode = document.createTextNode(line)
        range.insertNode(textNode)
        range.setStartAfter(textNode)
        range.collapse(true)
        lastNode = textNode
      }
    })

    // Move cursor to after the last inserted node
    if (lastNode) {
      range.setStartAfter(lastNode)
      range.collapse(true)
    }
    selection.removeAllRanges()
    selection.addRange(range)

    handleInput()

    // Scroll editor so the cursor (bottom of content) is visible
    if (editorRef.current) {
      editorRef.current.scrollTop = editorRef.current.scrollHeight
    }
  }
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
    const displayLength = newStorage.replace(/<@[^|]+\|([^>]+)>/g, '@$1').length
    setPlainTextLength(displayLength)

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

  // Auto-focus editor when a quote is set
  useEffect(() => {
    if (quote) {
      editorRef.current?.focus()
    }
  }, [quote])

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
    if ((!trimmedMessage && attachments.length === 0) || disableSend || disabled) {
      return
    }

    if (trimmedMessage.length > MAX_MESSAGE_LENGTH || getEditorText().length > MAX_MESSAGE_LENGTH) {
      toast.warning(`Message exceeds the ${MAX_MESSAGE_LENGTH.toLocaleString()} character limit`)
      return
    }

    if (trimmedMessage || attachments.length > 0) {
      const targetGroup = mentionedAgents.length > 0 ? undefined : (selectedGroup ?? undefined)
      const submittedAttachments = attachments.length > 0 ? attachments : undefined

      console.log('🚀 Submitting message (storage format):', trimmedMessage, 'targetGroup:', targetGroup, 'attachments:', attachments.length)

      setMessage('')
      setPlainTextLength(0)
      setAttachments([])
      attachmentsRef.current = []

      if (editorRef.current) {
        editorRef.current.innerHTML = ''
      }
      setShowAgentSuggestions(false)
      setSelectedAgentIndex(0)
      onClearQuote?.()

      // Submitted attachment blob URLs are NOT revoked here — they may
      // still be needed by the new-room handoff flow.  Instead,
      // sendUserMessage revokes them after the optimistic swap replaces
      // blob URLs with server URLs in the message store.
      onSubmit(trimmedMessage, targetGroup, quote, submittedAttachments)
    }
  }

  // Determine if message is ready to send — gate on trimmed storage format length
  // (onSubmit receives message.trim(), so measure the same string everywhere)
  const trimmedStorageLength = message.trim().length
  const isDisplayOverLimit = plainTextLength > MAX_MESSAGE_LENGTH
  const isStorageOverLimit = trimmedStorageLength > MAX_MESSAGE_LENGTH
  const isOverLimit = isDisplayOverLimit || isStorageOverLimit
  const isReadyToSend = (message.trim() || attachments.length > 0) && !disableSend && !disabled && !isOverLimit

  return (
    <div className="relative">
      {/* Agent suggestions dropdown */}
      {showAgentSuggestions && filteredAgents.length > 0 && (
        <div
          ref={suggestionsRef}
          className={cn(
            "absolute bottom-full left-4 right-4 mb-3 z-50",
            "bg-popover backdrop-blur-xl",
            "border border-border/50 shadow-xl rounded-2xl",
            "animate-in fade-in slide-in-from-bottom-3 duration-300"
          )}
        >
          {/* Header */}
          <div className="relative px-4 py-2.5 border-b border-border/30">
            <div className="flex items-center gap-2">
              <AtSign className="h-3.5 w-3.5 text-primary" />
              <span className="text-sm font-medium text-foreground">Mention an agent</span>
              <span className="ml-auto flex items-center gap-1 text-[11px] text-muted-foreground">
                <ChevronsUpDown className="h-3 w-3" />
                Scroll to view agents
              </span>
            </div>
          </div>

          {/* Agent list with scroll */}
          <div ref={listRef} className="py-1 max-h-52 overflow-y-auto">
            {filteredAgents.map((agent, index) => (
              <button
                key={agent.id}
                onClick={() => insertMention(agent)}
                className={cn(
                  "w-full text-left px-3 py-2.5 transition-all duration-100 flex items-center gap-3 mx-1 rounded-lg",
                  index === selectedAgentIndex
                    ? 'bg-accent text-accent-foreground'
                    : 'text-foreground hover:bg-muted'
                )}
                onMouseEnter={() => setSelectedAgentIndex(index)}
                style={{ width: 'calc(100% - 8px)' }}
              >
                {/* Agent avatar */}
                <div className="w-8 h-8 rounded-full shrink-0 overflow-hidden bg-muted flex items-center justify-center">
                  {agent.iconUrl ? (
                    <Image
                      src={agent.iconUrl}
                      alt={agent.name}
                      width={32}
                      height={32}
                      className="w-full h-full object-cover"
                      onError={(e) => {
                        e.currentTarget.style.display = 'none'
                        e.currentTarget.nextElementSibling?.classList.remove('hidden')
                      }}
                    />
                  ) : null}
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={getAgentAvatarUri(agent.id)} alt="" className={cn("w-full h-full", agent.iconUrl && "hidden")} />
                </div>

                <span className="font-medium truncate text-sm flex-1">
                  {agent.name}
                </span>

                {/* Keyboard hint for selected item */}
                {index === selectedAgentIndex && (
                  <span className="text-[10px] px-2 py-1 bg-muted rounded font-medium text-muted-foreground whitespace-nowrap">
                    Press Enter To Select
                  </span>
                )}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* Main input container with animated gradient border */}
      <div
        onDrop={(e) => {
          e.preventDefault()
          const files = Array.from(e.dataTransfer.files)
          if (files.length > 0) addFiles(files)
        }}
        onDragOver={(e) => e.preventDefault()}
        className={cn(
        "group/input relative flex flex-col rounded-3xl transition-all duration-500",
        "bg-gradient-to-b from-background via-background to-background/95",
        "shadow-xl hover:shadow-2xl",
        // Outer glow effects
        "before:absolute before:-inset-[1px] before:rounded-3xl before:p-[1px]",
        "before:bg-gradient-to-b before:from-border/80 before:via-border/50 before:to-border/80",
        "before:transition-all before:duration-500 before:-z-10",
        // Focus/hover gradient border
        "focus-within:before:from-primary/60 focus-within:before:via-primary/40 focus-within:before:to-primary/60",
        "hover:before:from-primary/40 hover:before:via-primary/20 hover:before:to-primary/40",
        // Shadow glow
        "hover:shadow-[0_8px_40px_-12px_rgba(var(--color-primary)/0.25)]",
        "focus-within:shadow-[0_8px_50px_-10px_rgba(var(--color-primary)/0.35)]",
        // Dark mode enhancements
        "dark:hover:shadow-[0_8px_50px_-10px_rgba(0,255,255,0.2)]",
        "dark:focus-within:shadow-[0_8px_60px_-8px_rgba(0,255,255,0.3)]"
      )}>
        {/* Inner container with actual border */}
        <div className="relative flex flex-col rounded-3xl bg-muted/70 dark:bg-muted/50 backdrop-blur-sm border border-border/50 overflow-hidden">
          {/* Top slot (e.g. HITL Questions panel) */}
          {topSlot}

          {/* Attachment previews */}
          <AttachmentPreview attachments={attachments} onRemove={removeAttachment} />

          {/* Expand/Collapse toggle - shown when content overflows or already expanded */}
          {(isOverflowing || isEditorExpanded) && (
            <button
              type="button"
              onClick={() => setIsEditorExpanded(prev => !prev)}
              className="absolute top-2.5 right-3 z-10 p-1.5 rounded-lg text-muted-foreground hover:text-foreground hover:bg-muted/80 transition-colors"
              title={isEditorExpanded ? 'Collapse editor' : 'Expand editor'}
            >
              {isEditorExpanded ? (
                <Minimize2 className="h-3.5 w-3.5" />
              ) : (
                <Maximize2 className="h-3.5 w-3.5" />
              )}
            </button>
          )}
          {/* Quote preview */}
          {quote && (
            <div className="mx-4 mt-3 flex items-start gap-2 rounded-lg bg-muted/60 px-3 py-2 text-sm">
              <div className="w-0.5 shrink-0 self-stretch rounded-full bg-primary" />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5 mb-0.5">
                  <Quote className="h-3 w-3 text-primary shrink-0" />
                  <span className="text-xs font-semibold text-primary truncate">
                    {quote.senderName}
                  </span>
                </div>
                <p className="text-xs text-muted-foreground line-clamp-2 break-words">
                  {quote.content}
                </p>
              </div>
              <button
                type="button"
                onClick={onClearQuote}
                className="shrink-0 p-0.5 rounded hover:bg-muted-foreground/20 text-muted-foreground hover:text-foreground transition-colors"
                aria-label="Remove quote"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          )}

          {/* Contenteditable div */}
          <div className="flex-1 px-5 py-3">
            <div
              ref={editorRef}
              contentEditable={!disabled}
              onInput={handleInput}
              onPaste={handlePaste}
              onKeyDown={handleKeyDown}
              className={cn(
                "w-full overflow-y-auto resize-none",
                "border-0 bg-transparent text-[15px] leading-7 text-foreground",
                "focus:outline-none placeholder-editor",
                isEditorExpanded ? "min-h-[200px] max-h-[60vh]" : "min-h-[28px] max-h-[200px]",
                disabled && "opacity-40 cursor-not-allowed"
              )}
              data-testid="chat-input"
              data-placeholder="Type a message... Use @ to mention agents"
              suppressContentEditableWarning
              style={{
                caretColor: 'hsl(var(--color-primary))',
                wordBreak: 'break-word',
                whiteSpace: 'pre-wrap',
              }}
            />
          </div>

          {/* Controls: Attach + @ + GroupSelector left, Send/Stop right */}
          <div className="flex items-center justify-between px-3 pb-3 pt-2">
            {/* Attach + @ + Group selector + Supervisor indicator (left) */}
            <div className="flex items-center gap-1 text-sm text-muted-foreground">
              <FileAttachmentButton
                onFiles={addFiles}
                disabled={disabled || sending || processing}
                supervisorMode={supervisorMode}
                onSupervisorChange={onSupervisorChange}
                debateMode={debateMode}
                onDebateModeChange={onDebateModeChange}
              />
              <Button
                type="button"
                variant="ghost"
                size="icon"
                disabled={disabled || sending || processing}
                className="h-8 w-8 rounded-full text-muted-foreground hover:text-primary transition-colors"
                title="Mention an agent (@)"
                onClick={() => {
                  if (!editorRef.current) return
                  editorRef.current.focus()
                  document.execCommand('insertText', false, '@')
                  handleInput()
                }}
              >
                <AtSign className="h-4 w-4" />
              </Button>
              {showGroupSelector && (
                <GroupSelector
                  selectedGroup={selectedGroup}
                  onGroupChange={onGroupChange || (() => { })}
                  groups={groups}
                  loadingGroups={loadingGroups}
                  roomAgentCount={roomAgentCount}
                  mentionedAgents={mentionedAgents}
                  onCreateGroup={onCreateGroup}
                  onEditGroup={onEditGroup}
                  onDeleteGroup={onDeleteGroup}
                  onEditRoomAgents={onEditRoomAgents}
                  agentNameMap={agentNameMap}
                  disabled={disabled}
                  isOverride={isOverride}
                  onClearOverride={onClearOverride}
                />
              )}
              {supervisorMode && onSupervisorChange && (
                <>
                  <div className="h-4 w-px bg-border mx-0.5" />
                  <TooltipProvider delayDuration={300}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          onClick={() => onSupervisorChange(false)}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-sm font-medium text-sky-400 hover:text-sky-300 hover:bg-sky-400/10 transition-colors"
                        >
                          <ShipWheel className="h-4.5 w-4.5" />
                          <span>Supervisor</span>
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="text-xs">
                        Supervisor understands your request better
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </>
              )}
              {debateMode && onDebateModeChange && (
                <>
                  <div className="h-4 w-px bg-border mx-0.5" />
                  <TooltipProvider delayDuration={300}>
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <button
                          type="button"
                          onClick={() => onDebateModeChange(false)}
                          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-sm font-medium text-purple-400 hover:text-purple-300 hover:bg-purple-400/10 transition-colors"
                        >
                          <Swords className="h-4.5 w-4.5" />
                          <span>Debate</span>
                        </button>
                      </TooltipTrigger>
                      <TooltipContent side="top" className="text-xs">
                        Agents will debate with each other
                      </TooltipContent>
                    </Tooltip>
                  </TooltipProvider>
                </>
              )}
            </div>

            {/* Send / Stop button (right) */}
            <div className="flex items-center gap-1">
              {isStorageOverLimit && !isDisplayOverLimit && plainTextLength < COUNTER_VISIBLE_THRESHOLD ? (
                <span
                  className="text-xs font-medium text-red-600 dark:text-red-400 transition-colors duration-200 mr-1"
                  data-testid="char-counter"
                >
                  Message too large (mentions)
                </span>
              ) : plainTextLength >= COUNTER_VISIBLE_THRESHOLD ? (
                <span
                  className={cn(
                    "text-xs font-medium tabular-nums transition-colors duration-200 mr-1",
                    isOverLimit
                      ? "text-red-600 dark:text-red-400"
                      : plainTextLength >= WARNING_THRESHOLD
                        ? "text-red-500/80 dark:text-red-400/80"
                        : "text-amber-600 dark:text-amber-400"
                  )}
                  data-testid="char-counter"
                >
                  {plainTextLength.toLocaleString()}/{MAX_MESSAGE_LENGTH.toLocaleString()}
                </span>
              ) : null}
            {sending ? (
              <div className="relative">
                <Button
                  disabled
                  size="icon"
                  className={cn(
                    "h-8 w-8 rounded-full p-0",
                    "bg-gradient-to-br from-primary to-primary/80"
                  )}
                  title="Sending message..."
                >
                  <div className="h-4 w-4 border-2 border-primary-foreground border-t-transparent rounded-full animate-spin" />
                </Button>
                <span className="absolute inset-0 rounded-full bg-primary/20 animate-ping" />
              </div>
            ) : cancelling && processing ? (
              <Button
                disabled
                size="icon"
                className={cn(
                  "h-8 w-8 rounded-full p-0",
                  "bg-destructive/60",
                )}
                title="Cancelling..."
              >
                <div className="h-4 w-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
              </Button>
            ) : processing ? (
              <Button
                onClick={onCancel}
                size="icon"
                className={cn(
                  "h-8 w-8 rounded-full p-0",
                  "bg-muted text-muted-foreground",
                  "hover:scale-105 active:scale-95 transition-all duration-200",
                )}
                title="Stop processing"
                data-testid="stop-processing"
              >
                <Square className="h-3.5 w-3.5 fill-current" />
              </Button>
            ) : (
              <Button
                onClick={handleSubmit}
                disabled={!isReadyToSend}
                size="icon"
                className={cn(
                  "h-8 w-8 rounded-full p-0",
                  "transition-all duration-300",
                  "hover:scale-105 active:scale-95",
                  "disabled:hover:scale-100 disabled:shadow-none disabled:cursor-default",
                  isReadyToSend
                    ? "bg-primary text-primary-foreground shadow-md shadow-primary/30 hover:shadow-lg hover:shadow-primary/40"
                    : "bg-primary/40 text-primary-foreground/70"
                )}
                title="Send message (Enter)"
              >
                <ArrowUp className="h-4 w-4" />
              </Button>
            )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
