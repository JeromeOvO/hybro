import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card'

interface UserAnswerCardProps {
  agentName: string
  question: string
  answer: string
}

export function UserAnswerCard({ agentName, question, answer }: UserAnswerCardProps) {
  return (
    <Card className="gap-3 py-3 shadow-none" data-testid="hitl-answer-card">
      <CardHeader className="gap-1 px-3">
        <CardTitle className="text-sm">Response to {agentName}</CardTitle>
        <CardDescription>{question}</CardDescription>
      </CardHeader>
      <CardContent className="break-words px-3 text-sm">{answer}</CardContent>
    </Card>
  )
}
