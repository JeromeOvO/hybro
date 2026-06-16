export const AGENT_PALETTE_DARK = [
  { name: 'slate-blue',  hsl: '215 30% 76%' },
  { name: 'soft-purple', hsl: '265 25% 78%' },
  { name: 'dusty-rose',  hsl: '340 28% 77%' },
  { name: 'warm-peach',  hsl: '25 35% 75%' },
  { name: 'muted-gold',  hsl: '45 28% 73%' },
  { name: 'sage-teal',   hsl: '170 25% 72%' },
  { name: 'soft-mint',   hsl: '145 22% 74%' },
  { name: 'calm-sky',    hsl: '195 28% 75%' },
] as const

export const AGENT_PALETTE_LIGHT = [
  { name: 'slate-blue',  hsl: '215 40% 45%' },
  { name: 'soft-purple', hsl: '265 35% 48%' },
  { name: 'dusty-rose',  hsl: '340 38% 47%' },
  { name: 'warm-peach',  hsl: '25 45% 44%' },
  { name: 'muted-gold',  hsl: '45 40% 40%' },
  { name: 'sage-teal',   hsl: '170 35% 38%' },
  { name: 'soft-mint',   hsl: '145 32% 40%' },
  { name: 'calm-sky',    hsl: '195 38% 43%' },
] as const

export const AGENT_PALETTE = AGENT_PALETTE_DARK

export function agentColorIndex(id: string): number {
  let hash = 0
  for (let i = 0; i < id.length; i++) hash = ((hash << 5) - hash + id.charCodeAt(i)) | 0
  return ((hash % AGENT_PALETTE.length) + AGENT_PALETTE.length) % AGENT_PALETTE.length
}

export function mentionColor(id: string): string {
  const idx = agentColorIndex(id)
  return `hsl(var(--agent-color-${idx}))`
}
