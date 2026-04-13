'use client'

interface HitlCompactCardProps {
  prompt: string
  answer: string
}

export function HitlCompactCard({ prompt, answer }: HitlCompactCardProps) {
  return (
    <div
      role="status"
      aria-label={`Resolved: ${prompt} — ${answer}`}
      className="border-l-2 border-green-500/30 pl-3 mt-2 py-1"
    >
      <p className="text-xs text-muted-foreground truncate">{prompt}</p>
      <div className="flex items-center gap-1.5 mt-0.5">
        <span className="w-1 h-1 rounded-full bg-green-500 shrink-0" />
        <span className="text-xs font-medium text-foreground">{answer}</span>
      </div>
    </div>
  )
}
