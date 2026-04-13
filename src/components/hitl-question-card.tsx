'use client'

interface HitlQuestionCardProps {
  prompt: string
}

export function HitlQuestionCard({ prompt }: HitlQuestionCardProps) {
  return (
    <div
      role="status"
      aria-label={`Agent needs input: ${prompt}`}
      className="border-l-2 border-yellow-500/30 pl-3 mt-2 py-1"
    >
      <span className="shimmer-text-yellow text-xs font-medium">Needs input</span>
      <p className="text-sm text-foreground/70 leading-relaxed mt-0.5">{prompt}</p>
    </div>
  )
}
