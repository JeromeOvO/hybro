const submissionsInFlight = new Set<string>()

export function hitlSubmissionFenceKey(conversationId: string, interactionId: string): string {
  return JSON.stringify([conversationId, interactionId])
}

export function acquireHitlSubmissionFence(
  conversationId: string,
  interactionId: string,
): () => void {
  const key = hitlSubmissionFenceKey(conversationId, interactionId)
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
