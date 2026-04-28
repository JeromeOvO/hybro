interface UserAnswerCardProps {
  agentName: string
  question: string
  answer: string
}

export function UserAnswerCard({ agentName, question, answer }: UserAnswerCardProps) {
  return (
    <div
      className="rounded-lg border px-3 py-2.5"
      style={{
        backgroundColor: 'var(--conversation-surface)',
        borderColor: 'var(--conversation-border)',
      }}
    >
      <div className="text-xs mb-1.5" style={{ color: 'var(--conversation-text-muted)' }}>
        Response to {agentName}
      </div>
      <div className="pl-3 text-sm mb-1" style={{ color: 'var(--conversation-text-muted)' }}>
        {question}
      </div>
      <div className="pl-3 text-sm" style={{ color: 'var(--conversation-text-secondary)' }}>
        {answer}
      </div>
    </div>
  )
}
