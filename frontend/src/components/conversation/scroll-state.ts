export interface ResolveScrollStateInput {
  atBottom: boolean
  programmatic: boolean
  previousScrollTop: number | null
  currentScrollTop: number
  wasPaused: boolean
}

export interface ResolveScrollStateOutput {
  programmatic: boolean
  paused: boolean
  clearNewContent: boolean
}

export function resolveScrollStateAfterEvent(input: ResolveScrollStateInput): ResolveScrollStateOutput {
  if (input.atBottom) {
    return { programmatic: false, paused: false, clearNewContent: true }
  }

  if (!input.programmatic) {
    return { programmatic: false, paused: true, clearNewContent: false }
  }

  const interrupted =
    input.previousScrollTop !== null &&
    input.currentScrollTop < input.previousScrollTop

  if (interrupted) {
    return { programmatic: false, paused: input.wasPaused, clearNewContent: false }
  }

  return {
    programmatic: true,
    paused: input.wasPaused,
    clearNewContent: false,
  }
}
