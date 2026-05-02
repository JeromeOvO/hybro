interface UserAnswerCardProps {
  agentName: string
  question: string
  answer: string
}

export function UserAnswerCard({ agentName, question, answer }: UserAnswerCardProps) {
  return (
    <div className="conversation-hitl-answer" data-testid="hitl-answer-card">
      <div className="conversation-hitl-answer-header">
        Response to {agentName}
      </div>
      <div className="conversation-hitl-answer-prompt">
        {question}
      </div>
      <div className="conversation-hitl-answer-value">
        {answer}
      </div>
    </div>
  )
}
