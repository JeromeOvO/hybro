'use client'

import { useState, useRef, useEffect } from 'react'
import { Send, Plus, Paperclip, Image as ImageIcon } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'

interface Agent {
  id: string
  name: string
}

interface RoomChatInputProps {
  onSubmit: (message: string) => void
  disabled?: boolean
  agents: Agent[]
}

export function RoomChatInput({ onSubmit, disabled, agents }: RoomChatInputProps) {
  const [message, setMessage] = useState('')
  const [displayMessage, setDisplayMessage] = useState('') // Message for display
  const [showAgentSuggestions, setShowAgentSuggestions] = useState(false)
  const [mentionQuery, setMentionQuery] = useState('')
  const [cursorPosition, setCursorPosition] = useState(0)
  const [selectedAgentIndex, setSelectedAgentIndex] = useState(0) // For keyboard navigation
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const imageInputRef = useRef<HTMLInputElement>(null)

  const filteredAgents = agents.filter(agent =>
    agent.name.toLowerCase().includes(mentionQuery.toLowerCase())
  ).slice(0, 5) // Limit to 5 agents

  // Reset selected index when filtered agents change
  useEffect(() => {
    setSelectedAgentIndex(0)
  }, [filteredAgents.length, mentionQuery])

  // Convert storage format to display format
  const convertToDisplayFormat = (content: string) => {
    return content.replace(/<@([^|]+)\|([^>]+)>/g, '@$2')
  }

  // Convert display format back to storage format (when needed)
  const convertToStorageFormat = (content: string, agents: Agent[]) => {
    let result = content
    agents.forEach(agent => {
      const displayMention = `@${agent.name}`
      const storageMention = `<@${agent.id}|${agent.name}>`
      result = result.replace(new RegExp(displayMention.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'g'), storageMention)
    })
    return result
  }

  // Update display message
  useEffect(() => {
    setDisplayMessage(convertToDisplayFormat(message))
  }, [message])

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newDisplayMessage = e.target.value
    const position = e.target.selectionStart
    
    setDisplayMessage(newDisplayMessage)
    setCursorPosition(position)

    // Check for @ mentions - search in display text
    const beforeCursor = newDisplayMessage.slice(0, position)
    const mentionMatch = beforeCursor.match(/@([^@\n]*)$/)
    
    if (mentionMatch) {
      setMentionQuery(mentionMatch[1])
      setShowAgentSuggestions(true)
    } else {
      setShowAgentSuggestions(false)
      setMentionQuery('')
      setSelectedAgentIndex(0)
    }

    // Sync update the actual stored message (convert back to storage format)
    setMessage(convertToStorageFormat(newDisplayMessage, agents))
  }

  const insertMention = (agent: Agent) => {
    const beforeCursor = displayMessage.slice(0, cursorPosition)
    const afterCursor = displayMessage.slice(cursorPosition)
    const beforeMention = beforeCursor.replace(/@[^@\n]*$/, '')
    
    // Insert in display format
    const newDisplayMessage = `${beforeMention}@${agent.name} ${afterCursor}`
    setDisplayMessage(newDisplayMessage)
    
    // Insert in storage format
    const newStorageMessage = `${beforeMention}<@${agent.id}|${agent.name}> ${afterCursor}`
    setMessage(newStorageMessage)
    
    setShowAgentSuggestions(false)
    setMentionQuery('')
    setSelectedAgentIndex(0)
    
    // Calculate new cursor position (after @agent_name)
    const newCursorPosition = beforeMention.length + `@${agent.name} `.length
    
    // Focus back to textarea and set cursor position
    setTimeout(() => {
      if (textareaRef.current) {
        textareaRef.current.focus()
        textareaRef.current.setSelectionRange(newCursorPosition, newCursorPosition)
        setCursorPosition(newCursorPosition)
      }
    }, 0)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
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
          // Let other keys pass through
          break
      }
    } else {
      // Normal textarea behavior when suggestions are not shown
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault()
        handleSubmit()
      }
    }
  }

  const handleSubmit = () => {
    if (message.trim()) {
      console.log('🚀 Submitting message (storage format):', message.trim())
      onSubmit(message.trim()) // Submit message in storage format
      setMessage('')
      setDisplayMessage('')
      setShowAgentSuggestions(false)
      setSelectedAgentIndex(0)
    }
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files) {
      // Handle file upload logic
      console.log('Files selected:', files)
    }
    e.target.value = ""
  }

  return (
    <div className="relative">
      {/* Agent suggestions dropdown */}
      {showAgentSuggestions && filteredAgents.length > 0 && (
        <div className="absolute bottom-full left-0 right-0 mb-2 bg-white dark:bg-gray-800 border border-gray-200 dark:border-gray-700 rounded-lg shadow-lg max-h-32 overflow-y-auto z-50">
          {filteredAgents.map((agent, index) => (
            <button
              key={agent.id}
              onClick={() => insertMention(agent)}
              className={`w-full text-left px-3 py-2 text-sm text-gray-900 dark:text-gray-100 transition-colors ${
                index === selectedAgentIndex
                  ? 'bg-blue-100 dark:bg-blue-900 text-blue-900 dark:text-blue-100'
                  : 'hover:bg-gray-100 dark:hover:bg-gray-700'
              }`}
            >
              @{agent.name}
            </button>
          ))}
        </div>
      )}

      <div className="relative flex flex-col rounded-3xl bg-background border border-border shadow-lg focus-within:border-primary/50 transition-all duration-200 hover:shadow-xl hover:border-primary/30">
        {/* Hidden file inputs */}
        <input
          ref={fileInputRef}
          type="file"
          className="hidden"
          onChange={handleFileSelect}
          multiple
          accept=".pdf,.doc,.docx,.txt,.csv,.json"
        />
        <input
          ref={imageInputRef}
          type="file"
          className="hidden"
          onChange={handleFileSelect}
          multiple
          accept="image/*"
        />

        {/* Textarea */}
        <div className="flex-1 p-4 pb-3">
          <Textarea
            ref={textareaRef}
            value={displayMessage} // Display converted message
            onChange={handleTextareaChange}
            onKeyDown={handleKeyDown}
            placeholder="Type a message... Use @ to mention agents"
            disabled={disabled}
            className="min-h-[50px] resize-none border-0 bg-transparent p-0 text-lg placeholder:text-muted-foreground focus-visible:ring-0 focus-visible:ring-offset-0"
            style={{ 
              height: "auto",
              maxHeight: "200px",
              overflow: "auto"
            }}
          />
        </div>

        {/* Controls */}
        <div className="flex items-center justify-between px-6 pb-4 pt-2">
          {/* Left side - Tools */}
          {/* <div className="flex items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button
                  variant="ghost"
                  size="lg"
                  disabled={disabled}
                  className="h-12 w-12 rounded-full p-0 hover:bg-muted"
                >
                  <Plus className="h-5 w-5" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent 
                align="start" 
                side="top" 
                sideOffset={12}
                className="bg-card border-border"
              >
                <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
                  <Paperclip className="h-4 w-4 mr-2" />
                  Attach file
                </DropdownMenuItem>
                <DropdownMenuItem onClick={() => imageInputRef.current?.click()}>
                  <ImageIcon className="h-4 w-4 mr-2" />
                  Upload image
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div> */}

          {/* Right side - Send */}
          <div className="flex items-center gap-2 ml-auto">
            <Button
              onClick={handleSubmit}
              disabled={disabled || !message.trim()}
              size="lg"
              className="h-12 w-12 rounded-full p-0"
            >
              <Send className="h-5 w-5" />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
