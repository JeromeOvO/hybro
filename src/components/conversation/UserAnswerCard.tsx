interface UserAnswerCardProps {
  agentName: string
  question: string
  answer: string
}

export function UserAnswerCard({ agentName, question, answer }: UserAnswerCardProps) {
  return (
    <div
      className="border"
      style={{
        borderRadius: 12,

        padding: '12px 14px',
        backgroundColor: 'hsl(var(--card))',
        borderColor: 'var(--conversation-border)',
      }}
    >
      <div className="text-[11px] mb-2" style={{ color: 'var(--conversation-text-muted)' }}>
        Response to {agentName}
      </div>
      <div className="text-xs mb-1.5" style={{ color: 'var(--conversation-text-muted)', paddingLeft: 10 }}>
        {question}
      </div>
      <div className="text-[13px]" style={{ color: 'var(--conversation-text-secondary)', paddingLeft: 10 }}>
        {answer}
      </div>
    </div>
  )
}
