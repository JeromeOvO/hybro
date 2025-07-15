"use client"

import * as React from "react"
import { 
  Plus, 
  Settings, 
  Mic, 
  Volume2, 
  Send, 
  Paperclip, 
  Image as ImageIcon,
  Code,
  Smile
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

interface ChatInputProps {
  placeholder?: string
  value?: string
  onChange?: (value: string) => void
  onSubmit?: (value: string) => void
  disabled?: boolean
  className?: string
  showTools?: boolean
  showVoice?: boolean
  showSend?: boolean
  maxRows?: number
  maxHeight?: number
  onFileUpload?: (files: FileList) => void
  onImageUpload?: (files: FileList) => void
}

export function ChatInput({
  placeholder = "Ask anything",
  value,
  onChange,
  onSubmit,
  disabled = false,
  className,
  showTools = true,
  showVoice = true,
  showSend = true,
  maxRows = 6,
  maxHeight = 300, // Increase default max height
  onFileUpload,
  onImageUpload,
}: ChatInputProps) {
  const [internalValue, setInternalValue] = React.useState("")
  const [isRecording, setIsRecording] = React.useState(false)
  const textareaRef = React.useRef<HTMLTextAreaElement>(null)
  const fileInputRef = React.useRef<HTMLInputElement>(null)
  const imageInputRef = React.useRef<HTMLInputElement>(null)

  const currentValue = value !== undefined ? value : internalValue
  const handleValueChange = value !== undefined ? onChange : setInternalValue

  // Auto-resize textarea
  React.useEffect(() => {
    const textarea = textareaRef.current
    if (textarea) {
      textarea.style.height = "auto"
      const scrollHeight = textarea.scrollHeight
      const lineHeight = parseInt(getComputedStyle(textarea).lineHeight) || 28 // Increase line height
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
      onSubmit(currentValue.trim())
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

  const handleVoiceClick = () => {
    setIsRecording(!isRecording)
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
      style={{ maxHeight: `${maxHeight + 80}px` }} // Increase overall height
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

      {/* Textarea - Upper section */}
      <div className="flex-1 p-4 pb-3"> {/* Increase padding from p-4 to p-6 */}
        <Textarea
          ref={textareaRef}
          value={currentValue}
          onChange={handleTextareaChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder}
          disabled={disabled}
          className={cn(
            "min-h-[50px] resize-none border-0 bg-transparent p-0 text-lg placeholder:text-muted-foreground focus-visible:ring-0 focus-visible:ring-offset-0", // Increase font size from text-base to text-lg, min height from 40px to 50px
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
      <div className="flex items-center justify-between px-6 pb-4 pt-2"> {/* Increase padding */}
        {/* Left side - Tools */}
        <div className="flex items-center gap-2"> {/* Increase gap */}
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
                    <Plus className="h-5 w-5" /> {/* Increase icon size */}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent 
                  align="start" 
                  side="bottom" // Change to prefer downward expansion
                  sideOffset={12} // Increase offset
                  alignOffset={-4}
                  className="w-52 bg-background/95 backdrop-blur-sm border-border shadow-lg" // Non-transparent background, follow theme
                  avoidCollisions={true}
                  collisionPadding={16} // Increase collision padding
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
            </>
          )}
        </div>

        {/* Right side - Voice and Send Controls */}
        <div className="flex items-center gap-2"> {/* Increase gap */}
          {showSend && (
            <Button
              onClick={handleSubmit}
              disabled={disabled || !hasContent}
              size="lg"
              className="h-12 w-12 rounded-full p-0 ml-1" // Increase button size
            >
              <Send className="h-5 w-5" /> {/* Increase icon size */}
            </Button>
          )}
        </div>
      </div>
    </div>
  )
} 