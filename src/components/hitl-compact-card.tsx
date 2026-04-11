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
      className="bg-background border border-border rounded-lg px-3 py-2 mt-2"
    >
      <p className="text-xs text-muted-foreground truncate mb-1">{prompt}</p>
      <div className="flex items-center gap-1.5">
        <span className="w-1 h-1 rounded-full bg-green-500 shrink-0" />
        <span className="text-xs font-medium text-foreground">{answer}</span>
      </div>
    </div>
  )
}
