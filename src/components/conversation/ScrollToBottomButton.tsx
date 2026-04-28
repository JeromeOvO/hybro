interface ScrollToBottomButtonProps {
  visible: boolean
  hasNewContent: boolean
  onClick: () => void
}

export function ScrollToBottomButton({ visible, hasNewContent, onClick }: ScrollToBottomButtonProps) {
  if (!visible) return null

  return (
    <button
      onClick={onClick}
      className="absolute bottom-4 left-1/2 -translate-x-1/2 z-10 rounded-full border px-3 py-1.5 text-xs font-medium transition-opacity"
      style={{
        backgroundColor: 'var(--conversation-surface)',
        borderColor: 'var(--conversation-border)',
        color: 'var(--conversation-text-secondary)',
      }}
      aria-label="Scroll to bottom"
    >
      ↓ Bottom
      {hasNewContent && (
        <span className="absolute -top-1 -right-1 w-2.5 h-2.5 rounded-full bg-blue-500" />
      )}
    </button>
  )
}
