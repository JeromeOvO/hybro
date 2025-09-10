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
      className="group relative overflow-hidden cursor-pointer
                 aspect-square w-full max-w-[260px]
                 backdrop-blur-sm
                 transition-all duration-300 ease-out
                 border border-transparent ring-0
                 hover:border hover:border-primary/70 dark:hover:border-primary/60
                 hover:bg-secondary/50 dark:hover:bg-muted/40 hover:shadow-2xl hover:shadow-primary/30
                 dark:hover:shadow-primary/20 dark:hover:shadow-2xl
                 hover:scale-[1.02] hover:-translate-y-1
                 before:absolute before:inset-0 before:bg-gradient-to-br 
                 before:from-primary/5 before:via-transparent before:to-accent/5
                 before:opacity-0 before:transition-opacity before:duration-300
                 hover:before:opacity-100
                 after:absolute after:inset-[2px] after:rounded-xl 
                 after:shadow-inner after:shadow-primary/8
                 dark:after:shadow-white/10 after:pointer-events-none
                 bg-secondary/40 dark:bg-muted/30 shadow-xl shadow-black/10 dark:shadow-black/50"
      onClick={handleCardClick}
    >
      <CardHeader className="pb-3 pt-4 text-center relative z-10">
        <div className="flex flex-col items-center gap-3">
          <div className="relative">
            <Avatar className="h-12 w-12 shadow-lg shadow-primary/20 dark:shadow-white/25
                             transition-all duration-300 ease-out
                             group-hover:shadow-xl group-hover:shadow-primary/35 
                             dark:group-hover:shadow-primary/35 group-hover:scale-110">
              <AvatarImage src={agent.agent_card.iconUrl || DEFAULT_AGENT_ICON} alt={agent.agent_card.name} />
              <AvatarFallback className="group-hover:bg-primary/20 transition-colors duration-300">
                <Bot className="h-6 w-6 group-hover:text-primary transition-colors duration-300" />
              </AvatarFallback>
            </Avatar>
            <div className="absolute -top-1 -right-1 transition-transform duration-300 group-hover:scale-110">
              <StatusIcon className={`${statusConfig.className} group-hover:shadow-lg`} />
            </div>
          </div>
        </div>
      </CardHeader>
      
      <CardContent className="px-6 pb-6 relative z-10">
        <div className="space-y-1">
            <CardTitle className="text-xl font-semibold text-center
                                transition-all duration-300 ease-out
                                group-hover:text-primary group-hover:scale-105">
              {agent.agent_card.name}
            </CardTitle>
          </div>
        <CardDescription className="text-center leading-relaxed line-clamp-3
                                   transition-all duration-300 ease-out
                                   group-hover:text-foreground/90">
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