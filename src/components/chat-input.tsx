"use client"

import * as React from "react"
import { 
  Plus, 
  Send, 
  Paperclip, 
  Image as ImageIcon,
} from "lucide-react"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"
import { GroupSelector } from "@/components/group-selector"
import type { AgentGroup } from "@/lib/types/agent-group"
import { BUILTIN_GROUP_ALL_AGENTS } from "@/lib/types/agent-group"

interface ChatInputProps {
  placeholder?: string
  value?: string
  onChange?: (value: string) => void
  onSubmit?: (value: string, targetGroup?: string) => void
  disabled?: boolean
  className?: string
  showTools?: boolean
  showVoice?: boolean
  showSend?: boolean
  showGroupSelector?: boolean
  maxRows?: number
  maxHeight?: number
  onFileUpload?: (files: FileList) => void
  onImageUpload?: (files: FileList) => void
  // Group selector props
  groups?: AgentGroup[]
  loadingGroups?: boolean
  selectedGroup?: string
  onGroupChange?: (groupId: string) => void
  roomAgentCount?: number
  onManageGroups?: () => void
}

export function ChatInput({
  placeholder = "Ask anything",
  value,
  onChange,
  onSubmit,
  disabled = false,
  className,
  showTools = false,
  showSend = true,
  showGroupSelector = false,
  maxRows = 6,
  maxHeight = 300,
  onFileUpload,
  onImageUpload,
  // Group selector props
  groups = [],
  loadingGroups = false,
  selectedGroup = BUILTIN_GROUP_ALL_AGENTS,
  onGroupChange,
  roomAgentCount = 0,
  onManageGroups,
}: ChatInputProps) {
  const [internalValue, setInternalValue] = React.useState("")
  const textareaRef = React.useRef<HTMLTextAreaElement>(null)
  const fileInputRef = React.useRef<HTMLInputElement>(null)
  const imageInputRef = React.useRef<HTMLInputElement>(null)

  const currentValue = value !== undefined ? value : internalValue
  const handleValueChange = value !== undefined ? onChange : setInternalValue

  React.useEffect(() => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = "auto"
      const scrollHeight = textarea.scrollHeight
      const lineHeight = parseInt(getComputedStyle(textarea).lineHeight) || 28
      const maxHeightFromRows = lineHeight * maxRows
      const finalMaxHeight = Math.min(maxHeightFromRows, maxHeight)
      textarea.style.height = `${Math.min(scrollHeight, finalMaxHeight)}px`
    }
  }, [currentValue, maxRows, maxHeight])

  const handleTextareaChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const newValue = e.target.value
    handleValueChange?.(newValue)
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const handleSubmit = () => {
    if (currentValue.trim() && onSubmit) {
      onSubmit(currentValue.trim(), selectedGroup)
      handleValueChange?.("")
    }
  }

  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && onFileUpload) {
      onFileUpload(files)
    }
    e.target.value = ""
  }

  const handleImageSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && onImageUpload) {
      onImageUpload(files)
    }
    e.target.value = ""
  }

  const hasContent = currentValue.trim().length > 0

  return (
    <div 
      className={cn(
        "relative flex flex-col rounded-3xl bg-background border border-border shadow-lg focus-within:border-primary/50 transition-all duration-200",
        "hover:shadow-xl hover:border-primary/30",
        "w-full max-w-7xl mx-auto",
        className
      )}
      style={{ maxHeight: `${maxHeight + 120}px` }}
    >
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
        onChange={handleImageSelect}
        multiple
        accept="image/*"
      />

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
              onManageGroups={onManageGroups}
              disabled={disabled}
            />
          </div>
        </div>
      )}

      {/* Textarea - Middle section */}
      <div className="flex-1 p-4 pb-3">
        <Textarea
          ref={textareaRef}
          value={currentValue}
          onChange={handleTextareaChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          className={cn(
            "min-h-[50px] resize-none border-0 bg-transparent p-0 text-lg placeholder:text-muted-foreground focus-visible:ring-0 focus-visible:ring-offset-0",
            "scrollbar-thin scrollbar-thumb-muted-foreground/20 scrollbar-track-transparent w-full"
          )}
          style={{ 
            height: "auto",
            maxHeight: `${maxHeight}px`,
            overflow: "auto"
          }}
        />
      </div>

      {/* Controls - Lower section */}
      <div className="flex items-center justify-between px-6 pb-4 pt-2">
        {/* Left side - Tools */}
        <div className="flex items-center gap-2">
          {showTools && (
            <>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button
                    variant="ghost"
                    size="lg"
                    disabled={disabled}
                    className="h-12 w-12 rounded-full p-0 hover:bg-muted"
                  >
                    <Plus className="h-5 w-5 icon-action" />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent 
                  align="start" 
                  side="bottom"
                  sideOffset={12}
                  alignOffset={-4}
                  className="w-52 bg-background/95 backdrop-blur-sm shadow-lg"
                  avoidCollisions={true}
                  collisionPadding={16}
                >
                  <DropdownMenuItem onClick={() => fileInputRef.current?.click()}>
                    <Paperclip className="h-4 w-4 mr-2 icon-neutral" />
                    Attach file
                  </DropdownMenuItem>
                  <DropdownMenuItem onClick={() => imageInputRef.current?.click()}>
                    <ImageIcon className="h-4 w-4 mr-2 icon-info" />
                    Upload image
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </>
          )}
        </div>

        {/* Right side - Send Controls */}
        <div className="flex items-center gap-2">
          {showSend && (
            <Button
              onClick={handleSubmit}
              disabled={disabled || !hasContent}
              size="lg"
              className="h-12 w-12 rounded-full p-0 ml-1"
            >
              <Send className="h-5 w-5 icon-action" />
            </Button>
          )}
        </div>
      </div>
    </div>
  )
}
