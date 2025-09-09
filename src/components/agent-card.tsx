import { Bot, CircleCheck, CircleMinus, PartyPopper, XCircle } from "lucide-react"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { useRouter } from "next/navigation"
import type { Agent, AgentCard } from '@/lib/types'

interface AgentCardProps {
  agent: Agent
}

const DEFAULT_AGENT_ICON = 'https://api.example.com/support-agent'

export function AgentCard({ 
  agent, 
}: AgentCardProps) {
  const router = useRouter()

  const getStatusIcon = (status: Agent["agent_status"]) => {
    const base = "inline-flex w-5 h-5 items-center justify-center";
  
    switch (status) {
      case "active":
        return {
          icon: CircleCheck,
          className: [
            base,
            "icon-success"
          ].join(" "),
        };
  
      case "inactive":
        return {
          icon: CircleMinus,
          className: [
            base,
            "icon-error"
          ].join(" "),
        };
  
      default:
        return {
          icon: XCircle,
          className: [
            base,
            "icon-error"
          ].join(" "),
        };
    }
  };


  const statusConfig = getStatusIcon(agent.agent_status)
  const StatusIcon = statusConfig.icon

  // Handle card click
  const handleCardClick = () => {
    router.push(`/agent/profile/${agent.agent_id}`)
  }

  return (
    <Card 
      className="group hover:shadow-lg transition-all duration-200 hover:border-primary/50 cursor-pointer
             aspect-square w-full max-w-[260px]"
      onClick={handleCardClick}
    >
      <CardHeader className="pb-3 pt-4 text-center">
        <div className="flex flex-col items-center gap-3">
          <div className="relative">
            <Avatar className="h-12 w-12 ring-2 ring-border shadow-sm">
              <AvatarImage src={agent.agent_card.iconUrl || DEFAULT_AGENT_ICON} alt={agent.agent_card.name} />
              <AvatarFallback>
                <Bot className="h-6 w-6" />
              </AvatarFallback>
            </Avatar>
            <div className="absolute -top-1 -right-1">
              <StatusIcon className={`${statusConfig.className}`} />
            </div>
          </div>
        </div>
      </CardHeader>
      
      <CardContent className="px-6 pb-6">
        <div className="space-y-1">
            <CardTitle className="text-xl font-semibold text-center">
              {agent.agent_card.name}
            </CardTitle>
          </div>
        <CardDescription className="text-center leading-relaxed line-clamp-3">
          {agent.agent_card.description}
        </CardDescription>
      </CardContent>
    </Card>
  )
}

export function StatsCards({ agents }: { agents: Agent[] }) {
  const totalAgents = agents.length

  return (
    <div className="flex justify-center">
      <Card
        className="@container/card border-none bg-transparent shadow-none flex flex-col items-center gap-3 px-8 py-6 w-52"
      >
        <PartyPopper className="w-7 h-7 icon-action" />
        <CardDescription className="font-medium text-muted-foreground whitespace-nowrap">
          Total&nbsp;Agents
        </CardDescription>
        <CardTitle className="text-5xl font-bold tabular-nums @[270px]/card:text-6xl">
          {totalAgents}
        </CardTitle>
      </Card>
    </div>
  )
}