import { ArrowDown } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

interface ScrollToBottomButtonProps {
  visible: boolean
  hasNewContent: boolean
  onClick: () => void
}

export function ScrollToBottomButton({ visible, hasNewContent, onClick }: ScrollToBottomButtonProps) {
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={onClick}
      className={cn(
        "absolute bottom-4 left-1/2 -translate-x-1/2 h-9 w-9 p-0 rounded-full bg-muted/80 backdrop-blur-sm shadow-md hover:bg-muted hover:shadow-lg transition-all duration-200 z-10",
        visible
          ? "opacity-100 scale-100"
          : "opacity-0 scale-90 pointer-events-none"
      )}
      aria-label="Scroll to bottom"
      tabIndex={visible ? 0 : -1}
    >
      <ArrowDown className="h-4 w-4" />
      {hasNewContent && (
        <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-blue-500" />
      )}
    </Button>
  )
}
