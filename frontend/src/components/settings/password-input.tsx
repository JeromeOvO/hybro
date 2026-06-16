"use client"

import { useState } from "react"
import { Eye, EyeOff } from "lucide-react"

import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

interface PasswordInputProps
  extends Omit<React.ComponentProps<typeof Input>, "type"> {
  /** Override the controlled visibility state (uncontrolled by default) */
  show?: boolean
  onShowChange?: (show: boolean) => void
}

export function PasswordInput({
  className,
  show: controlledShow,
  onShowChange,
  ...props
}: PasswordInputProps) {
  const [internalShow, setInternalShow] = useState(false)
  const isVisible = controlledShow ?? internalShow

  function toggleVisibility() {
    const next = !isVisible
    setInternalShow(next)
    onShowChange?.(next)
  }

  return (
    <div className="relative">
      <Input
        type={isVisible ? "text" : "password"}
        className={cn("pr-10", className)}
        {...props}
      />
      <button
        type="button"
        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
        onClick={toggleVisibility}
        tabIndex={-1}
        aria-label={isVisible ? "Hide password" : "Show password"}
      >
        {isVisible ? (
          <EyeOff className="h-4 w-4" />
        ) : (
          <Eye className="h-4 w-4" />
        )}
      </button>
    </div>
  )
}
