const submissionsInFlight = new Set<string>()

export function hitlSubmissionFenceKey(roomId: string, interactionId: string): string {
  return JSON.stringify([roomId, interactionId])
}

export function acquireHitlSubmissionFence(
  roomId: string,
  interactionId: string,
): () => void {
  const key = hitlSubmissionFenceKey(roomId, interactionId)
  if (submissionsInFlight.has(key)) {
    throw new Error('This questionnaire is already being submitted.')
  }
  submissionsInFlight.add(key)
  let released = false
  return () => {
    if (released) return
    released = true
    submissionsInFlight.delete(key)
  }
}
