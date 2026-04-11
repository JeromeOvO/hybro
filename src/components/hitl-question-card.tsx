'use client'

interface HitlQuestionCardProps {
  prompt: string
}

export function HitlQuestionCard({ prompt }: HitlQuestionCardProps) {
  return (
    <div
      role="status"
      aria-label={`Agent needs input: ${prompt}`}
      className="bg-background border border-yellow-500/20 rounded-lg px-3 py-3 mt-2"
    >
      <div className="flex items-center gap-1.5 mb-2">
        <span className="shimmer-text-yellow text-xs font-medium">Needs input</span>
      </div>
      <p className="text-sm text-foreground/80 leading-relaxed">{prompt}</p>
    </div>
  )
}
