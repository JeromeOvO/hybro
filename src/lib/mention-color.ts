const PALETTE = [
  'hsl(220 70% 72%)',  // blue
  'hsl(270 60% 75%)',  // purple
  'hsl(340 65% 72%)',  // rose
  'hsl(25 80% 68%)',   // peach
  'hsl(45 60% 65%)',   // amber
  'hsl(170 55% 62%)',  // teal
  'hsl(150 45% 65%)',  // mint
  'hsl(200 70% 70%)',  // sky
]

export function mentionColor(id: string): string {
  let hash = 0
  for (let i = 0; i < id.length; i++) hash = ((hash << 5) - hash + id.charCodeAt(i)) | 0
  return PALETTE[((hash % PALETTE.length) + PALETTE.length) % PALETTE.length]
}
