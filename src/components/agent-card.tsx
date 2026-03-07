import Image from "next/image"
import {
  Bot,
} from "lucide-react"
import {
  Card,
  CardDescription,
  CardTitle,
} from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { useRouter } from "next/navigation"
import { cn } from "@/lib/utils"
import type { Agent } from '@/lib/types'
import { deduplicateIcons } from '@/lib/agent-icon-utils'
import { AgentSourceBadge } from './agent-source-badge'

interface AgentCardProps {
  agent: Agent
}

export function AgentCard({ agent }: AgentCardProps) {
  const router = useRouter()

  const handleCardClick = () => {
    router.push(`/c/agents/${agent.agent_id}`)
  }

  const isActive = agent.agent_status === "active"
  const isHubOffline = agent.source === 'hub' && !agent.is_hub_online
  const allModes = [
    ...(agent.agent_card.defaultInputModes ?? []),
    ...(agent.agent_card.defaultOutputModes ?? [])
  ]
  const modeIcons = deduplicateIcons(allModes)

  return (
    <Card
      className={cn(
        "group relative overflow-hidden cursor-pointer",
        "w-full",
        "backdrop-blur-sm",
        "transition-all duration-300 ease-out",
        "border border-primary/20 dark:border-primary/15 ring-0",
        "hover:border-primary/80 dark:hover:border-primary/70",
        "hover:bg-secondary/50 dark:hover:bg-muted/40 hover:scale-[1.01] hover:-translate-y-0.5",
        "before:absolute before:inset-0 before:bg-linear-to-br",
        "before:from-primary/5 before:via-transparent before:to-accent/5",
        "before:opacity-0 before:transition-opacity before:duration-300",
        "hover:before:opacity-100",
        "bg-secondary/40 dark:bg-muted/30 shadow-sm hover:shadow-md hover:dark:shadow-black/30",
        isHubOffline && "opacity-50"
      )}
      onClick={handleCardClick}
    >
      <div className="grid grid-cols-4 gap-1 p-[2px] relative z-10">
        <div className="col-span-1 flex items-center justify-center">
          <div className="relative">
            <Avatar className="h-12 w-12 rounded-md shadow-md shadow-primary/10 dark:shadow-white/10
                               transition-all duration-300 ease-out
                               group-hover:shadow-lg group-hover:shadow-primary/20
                               dark:group-hover:shadow-primary/20">
              <AvatarImage src={agent.agent_card.iconUrl || undefined} alt={agent.agent_card.name} className="rounded-md" />
              <AvatarFallback className="rounded-md group-hover:bg-primary/20 transition-colors duration-300">
                <Bot className="h-6 w-6 group-hover:text-primary transition-colors duration-300" />
              </AvatarFallback>
            </Avatar>
            <span
              className={cn(
                "absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-[1.5px] border-background",
                isHubOffline
                  ? "bg-muted-foreground/30 animate-pulse"
                  : isActive ? "bg-green-500" : "bg-muted-foreground/30"
              )}
            />
          </div>
        </div>

        <div className="col-span-2 flex flex-col justify-center gap-0 min-w-0">
          <CardTitle className="text-sm font-semibold
                                transition-colors duration-300 ease-out
                                group-hover:text-primary leading-tight truncate">
            {agent.agent_card.name}
          </CardTitle>

          {agent.agent_card.description && (
            <p className="text-xs text-muted-foreground line-clamp-1 leading-snug">
              {agent.agent_card.description}
            </p>
          )}

          {modeIcons.length > 0 && (
            <div className="flex items-center gap-1.5">
              {modeIcons.map((Icon, i) => (
                <Icon key={i} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
              ))}
            </div>
          )}
        </div>

        <div className="col-span-1 flex items-center justify-center">
          <AgentSourceBadge
            source={agent.source}
            isHubOnline={agent.is_hub_online}
            className="h-3.5 w-3.5"
          />
        </div>
      </div>
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
        <Image
          src="/favicon.svg"
          alt="Hybro logo"
          width={28}
          height={28}
          className="w-9 h-9 text-icon-action"
          priority
        />
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