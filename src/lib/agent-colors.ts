/**
 * Agent color utilities for consistent visual identification
 * Each agent gets a unique color based on their ID hash
 */

// Color palette for agent identification - distinguishable and accessible
export const AGENT_COLOR_PALETTE = [
  {
    bg: 'bg-blue-50 dark:bg-blue-950',
    border: 'border-blue-200 dark:border-blue-800',
    accent: 'bg-blue-500',
    text: 'text-blue-700 dark:text-blue-300',
  },
  {
    bg: 'bg-emerald-50 dark:bg-emerald-950',
    border: 'border-emerald-200 dark:border-emerald-800',
    accent: 'bg-emerald-500',
    text: 'text-emerald-700 dark:text-emerald-300',
  },
  {
    bg: 'bg-amber-50 dark:bg-amber-950',
    border: 'border-amber-200 dark:border-amber-800',
    accent: 'bg-amber-500',
    text: 'text-amber-700 dark:text-amber-300',
  },
  {
    bg: 'bg-rose-50 dark:bg-rose-950',
    border: 'border-rose-200 dark:border-rose-800',
    accent: 'bg-rose-500',
    text: 'text-rose-700 dark:text-rose-300',
  },
  {
    bg: 'bg-violet-50 dark:bg-violet-950',
    border: 'border-violet-200 dark:border-violet-800',
    accent: 'bg-violet-500',
    text: 'text-violet-700 dark:text-violet-300',
  },
  {
    bg: 'bg-cyan-50 dark:bg-cyan-950',
    border: 'border-cyan-200 dark:border-cyan-800',
    accent: 'bg-cyan-500',
    text: 'text-cyan-700 dark:text-cyan-300',
  },
  {
    bg: 'bg-orange-50 dark:bg-orange-950',
    border: 'border-orange-200 dark:border-orange-800',
    accent: 'bg-orange-500',
    text: 'text-orange-700 dark:text-orange-300',
  },
  {
    bg: 'bg-pink-50 dark:bg-pink-950',
    border: 'border-pink-200 dark:border-pink-800',
    accent: 'bg-pink-500',
    text: 'text-pink-700 dark:text-pink-300',
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

