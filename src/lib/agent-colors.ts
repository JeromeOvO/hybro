/**
 * Agent color utilities for consistent visual identification
 * Each agent gets a unique color based on their ID hash
 */

// Futuristic yet simple palette for both light and dark themes
// Using white/very light backgrounds in light mode for better readability
export const AGENT_COLOR_PALETTE = [
  {
    bg: 'bg-white dark:bg-cyan-950',
    border: 'border-cyan-300 dark:border-cyan-800',
    accent: 'bg-cyan-400 dark:bg-cyan-300',
    text: 'text-cyan-700 dark:text-cyan-300',
    content: 'text-slate-700 dark:text-slate-200',
  },
  {
    bg: 'bg-white dark:bg-sky-950',
    border: 'border-sky-300 dark:border-sky-800',
    accent: 'bg-sky-400 dark:bg-sky-300',
    text: 'text-sky-700 dark:text-sky-300',
    content: 'text-slate-700 dark:text-slate-200',
  },
  {
    bg: 'bg-white dark:bg-indigo-950',
    border: 'border-indigo-300 dark:border-indigo-800',
    accent: 'bg-indigo-400 dark:bg-indigo-300',
    text: 'text-indigo-700 dark:text-indigo-300',
    content: 'text-slate-700 dark:text-slate-200',
  },
  {
    bg: 'bg-white dark:bg-emerald-950',
    border: 'border-emerald-300 dark:border-emerald-800',
    accent: 'bg-emerald-400 dark:bg-emerald-300',
    text: 'text-emerald-700 dark:text-emerald-300',
    content: 'text-slate-700 dark:text-slate-200',
  },
  {
    bg: 'bg-white dark:bg-fuchsia-950',
    border: 'border-fuchsia-300 dark:border-fuchsia-800',
    accent: 'bg-fuchsia-400 dark:bg-fuchsia-300',
    text: 'text-fuchsia-700 dark:text-fuchsia-300',
    content: 'text-slate-700 dark:text-slate-200',
  },
  {
    bg: 'bg-white dark:bg-amber-950',
    border: 'border-amber-300 dark:border-amber-800',
    accent: 'bg-amber-400 dark:bg-amber-300',
    text: 'text-amber-700 dark:text-amber-300',
    content: 'text-slate-700 dark:text-slate-200',
  },
  {
    bg: 'bg-white dark:bg-lime-950',
    border: 'border-lime-300 dark:border-lime-800',
    accent: 'bg-lime-400 dark:bg-lime-300',
    text: 'text-lime-700 dark:text-lime-300',
    content: 'text-slate-700 dark:text-slate-200',
  },
  {
    bg: 'bg-white dark:bg-rose-950',
    border: 'border-rose-300 dark:border-rose-800',
    accent: 'bg-rose-400 dark:bg-rose-300',
    text: 'text-rose-700 dark:text-rose-300',
    content: 'text-slate-700 dark:text-slate-200',
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

