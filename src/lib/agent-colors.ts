/**
 * Agent color utilities for consistent visual identification
 * Each agent gets a unique color based on their ID hash
 */

// Modern subtle palette — light tinted backgrounds with gentle borders.
// Light mode: soft pastel tint (50/70) | Dark mode: low-chroma mid-tone at 8% opacity.
// Follows Linear/Vercel design principles for readability and visual comfort.
export const AGENT_COLOR_PALETTE = [
  {
    bg: 'bg-sky-50/70 dark:bg-sky-500/8',
    border: 'border-sky-200 dark:border-sky-500/20',
    accent: 'bg-sky-500 dark:bg-sky-400',
    text: 'text-sky-700 dark:text-sky-400',
    content: 'text-slate-700 dark:text-slate-300',
  },
  {
    bg: 'bg-violet-50/70 dark:bg-violet-500/8',
    border: 'border-violet-200 dark:border-violet-500/20',
    accent: 'bg-violet-500 dark:bg-violet-400',
    text: 'text-violet-700 dark:text-violet-400',
    content: 'text-slate-700 dark:text-slate-300',
  },
  {
    bg: 'bg-teal-50/70 dark:bg-teal-500/8',
    border: 'border-teal-200 dark:border-teal-500/20',
    accent: 'bg-teal-500 dark:bg-teal-400',
    text: 'text-teal-700 dark:text-teal-400',
    content: 'text-slate-700 dark:text-slate-300',
  },
  {
    bg: 'bg-rose-50/70 dark:bg-rose-500/8',
    border: 'border-rose-200 dark:border-rose-500/20',
    accent: 'bg-rose-500 dark:bg-rose-400',
    text: 'text-rose-700 dark:text-rose-400',
    content: 'text-slate-700 dark:text-slate-300',
  },
  {
    bg: 'bg-amber-50/70 dark:bg-amber-500/8',
    border: 'border-amber-200 dark:border-amber-500/20',
    accent: 'bg-amber-600 dark:bg-amber-400',
    text: 'text-amber-700 dark:text-amber-400',
    content: 'text-slate-700 dark:text-slate-300',
  },
  {
    bg: 'bg-emerald-50/70 dark:bg-emerald-500/8',
    border: 'border-emerald-200 dark:border-emerald-500/20',
    accent: 'bg-emerald-500 dark:bg-emerald-400',
    text: 'text-emerald-700 dark:text-emerald-400',
    content: 'text-slate-700 dark:text-slate-300',
  },
  {
    bg: 'bg-indigo-50/70 dark:bg-indigo-500/8',
    border: 'border-indigo-200 dark:border-indigo-500/20',
    accent: 'bg-indigo-500 dark:bg-indigo-400',
    text: 'text-indigo-700 dark:text-indigo-400',
    content: 'text-slate-700 dark:text-slate-300',
  },
  {
    bg: 'bg-pink-50/70 dark:bg-pink-500/8',
    border: 'border-pink-200 dark:border-pink-500/20',
    accent: 'bg-pink-500 dark:bg-pink-400',
    text: 'text-pink-700 dark:text-pink-400',
    content: 'text-slate-700 dark:text-slate-300',
  },
]

/**
 * Generate a consistent hash from a string
 */
function hashString(str: string): number {
  let hash = 0
  for (let i = 0; i < str.length; i++) {
    const char = str.charCodeAt(i)
    hash = ((hash << 5) - hash) + char
    hash = hash & hash // Convert to 32bit integer
  }
  return Math.abs(hash)
}

/**
 * Get consistent color classes for an agent based on their ID
 */
export function getAgentColorClasses(agentId: string) {
  const index = hashString(agentId) % AGENT_COLOR_PALETTE.length
  return AGENT_COLOR_PALETTE[index]
}

/**
 * Get agent initials from name (max 2 characters)
 */
export function getAgentInitials(agentName: string): string {
  const words = agentName.trim().split(/\s+/)
  if (words.length >= 2) {
    return (words[0][0] + words[1][0]).toUpperCase()
  }
  return agentName.slice(0, 2).toUpperCase()
}

