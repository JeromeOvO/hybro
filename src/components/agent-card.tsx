import { Badge, Bot, Calendar, Settings, Star, Users, TrendingDown, TrendingUp, CheckCircle, Heart, XCircle, PartyPopper, HardDriveDownload, HardDriveUpload, HardDrive, Bell, HistoryIcon, Blocks, ArrowDownUp, MessageCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Stream } from "stream"
import { useRouter } from "next/navigation"

// Update imports to use unified type definitions
import type { Agent, AgentSkill, AgentCapabilities, AgentCard } from '@/lib/types'

// Remove original type definitions, use imported types directly
// API response data structure
export interface AgentsResponse {
  agent_id: string | null
  agent_card: AgentCard | null
  agent: Agent | null
  agents: Agent[]
  success: boolean
  error: string | null
  status_code: number
}

interface AgentCardProps {
  agent: Agent
}

const DEFAULT_AGENT_ICON = 'https://api.example.com/support-agent'

export function AgentCard({ 
  agent, 
}: AgentCardProps) {
  const router = useRouter()

  const getStatusIcon = (status: Agent["agent_status"]) => {
    const base =
      "inline-flex w-6 h-6 items-center justify-center rounded-full text-[1.125rem]";
  
    switch (status) {
      case "active":
        return {
          icon: CheckCircle,
          className: [
            base,
            "bg-green-50 text-green-600 ring-2 ring-green-500/20",
            "dark:bg-green-900/30 dark:text-green-300 dark:ring-green-400/30",
            "shadow-sm"
          ].join(" "),
        };
  
      case "inactive":
        return {
          icon: XCircle,
          className: [
            base,
            "bg-gray-50 text-gray-500 ring-1 ring-inset ring-gray-400/20",
            "dark:bg-gray-800/50 dark:text-gray-300 dark:ring-gray-700/40"
          ].join(" "),
        };
  
      default:
        return {
          icon: XCircle,
          className: [
            base,
            "bg-red-50 text-red-600 ring-2 ring-red-500/20",
            "dark:bg-red-900/30 dark:text-red-300 dark:ring-red-400/30"
          ].join(" "),
        };
    }
  };

  const getStatusText = (status: Agent['agent_status']) => {
    switch (status) {
      case 'active':
        return 'Active'
      case 'inactive':
        return 'Inactive'
      default:
        return 'Unknown'
    }
  }
  
  const successRate = agent.call_count && agent.call_success_count && agent.call_count > 0 
    ? ((agent.call_success_count / agent.call_count) * 100).toFixed(1)
    : '0'

  const statusConfig = getStatusIcon(agent.agent_status)
  const StatusIcon = statusConfig.icon

  // Handle card click
  const handleCardClick = () => {
    router.push(`/agent/profile/${agent.agent_id}`)
  }

  return (
    <Card 
      className="group hover:shadow-lg transition-all duration-200 hover:border-primary/50 cursor-pointer"
      onClick={handleCardClick}
    >
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between">
          <div className="flex items-center gap-3">
            <Avatar className="h-10 w-10">
              <AvatarImage src={agent.agent_card.iconUrl || DEFAULT_AGENT_ICON} alt={agent.agent_card.name} />
              <AvatarFallback>
                <Bot className="h-5 w-5" />
              </AvatarFallback>
            </Avatar>
            <div className="flex-1">
              <CardTitle className="text-lg">{agent.agent_card.name}</CardTitle>
              <CardDescription className="text-sm">
                v{agent.agent_card.version}
              </CardDescription>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge className={statusConfig.className}>
              <StatusIcon className="h-4 w-4 mr-1" />
              {getStatusText(agent.agent_status)}
            </Badge>
          </div>
        </div>
      </CardHeader>
      
      <CardContent className="pb-3">
        <CardDescription className="mb-3 line-clamp-2">
          {agent.agent_card.description}
        </CardDescription>
        
        <div className="flex items-center gap-4 text-sm text-muted-foreground mb-3">
          <div className="flex items-center gap-1">
            <Users className="h-4 w-4" />
            <span>{agent.call_count} calls</span>
          </div>
          <div className="flex items-center gap-1">
            <PartyPopper className="h-4 w-4" />
            <span>{successRate}% success</span>
          </div>
          <div className="flex items-center gap-1">
            <Heart className="h-4 w-4" />
            <span>{agent.like_count} likes</span>
          </div>
        </div>
        
        {agent.agent_card.skills && agent.agent_card.skills.length > 0 && (
          <div className="flex flex-wrap gap-1">
                <Button
                variant="outline"
                size="sm"
                >
                <HardDriveDownload className="h-4 w-4 mr-2" />
                InputMode:  {agent.agent_card.skills[0].inputModes || 'NULL'}
                </Button>  
                <Button
                variant="outline"
                size="sm"
                >
                <HardDriveUpload className="h-4 w-4 mr-2" />
                OutputMode:  {agent.agent_card.skills[0].outputModes || 'NULL'}
                </Button>  
          </div>
        )}
      </CardContent>
      
      <CardFooter className="pt-3 grid grid-cols-2 gap-2">
        <Button
          variant="outline"
          size="sm"
          className="text-xs"
        >
          <Blocks className="h-3 w-3 mr-1" />
          Extensions: {agent.agent_card.capabilities.extensions?.map(extension => extension.uri).join(', ')}
        </Button>   
        <Button
          variant="outline"
          size="sm"
          className="text-xs"
        >
          <ArrowDownUp className="h-3 w-3 mr-1" />
          Streaming: {agent.agent_card.capabilities.streaming ? 'Yes' : 'No'}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="text-xs"
        >
          <Bell className="h-3 w-3 mr-1" />
          PushNotifications: {agent.agent_card.capabilities.pushNotifications ? 'Yes' : 'No'}
        </Button>
        <Button
          variant="outline"
          size="sm"
          className="text-xs"
        >
          <HistoryIcon className="h-3 w-3 mr-1" />
          StateTransitionHistory: {agent.agent_card.capabilities.stateTransitionHistory ? 'Yes' : 'No'}
        </Button>
      </CardFooter>
    </Card>
  )
}

export function StatsCards({ agents }: { agents: Agent[] }) {
  const totalAgents = agents.length
  const activeAgents = agents.filter(a => a.agent_status === 'active').length
  const totalCalls = agents.reduce((sum, agent) => sum + (agent.call_count || 0), 0)
  const totalLikes = agents.reduce((sum, agent) => sum + (agent.like_count || 0), 0)
  const totalProviders = agents.reduce((sum, agent) => sum + (agent.agent_card.provider ? 1 : 0), 0)

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>Total Agents</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {totalAgents}
          </CardTitle>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            Registered agents <TrendingUp className="size-4" />
          </div>
          <div className="text-muted-foreground">
            Total agents in network
          </div>
        </CardFooter>
      </Card>
      
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>Active Agents</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {activeAgents}
          </CardTitle>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            Ready for interaction <TrendingUp className="size-4" />
          </div>
          <div className="text-muted-foreground">
            {totalAgents > 0 ? Math.round((activeAgents / totalAgents) * 100) : 0}% availability rate
          </div>
        </CardFooter>
      </Card>
      
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>Total Calls</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {totalCalls.toLocaleString()}
          </CardTitle>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            Agent interactions <TrendingUp className="size-4" />
          </div>
          <div className="text-muted-foreground">
            Total calls made
          </div>
        </CardFooter>
      </Card>
      
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>Providers</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            {totalProviders}
          </CardTitle>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            Total providers <TrendingUp className="size-4" />
          </div>
          <div className="text-muted-foreground">
            Total providers in network
          </div>
        </CardFooter>
      </Card>
    </div>
  )
}

export function SectionCards() {
  return (
    <div className="*:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card dark:*:data-[slot=card]:bg-card grid grid-cols-1 gap-4 px-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:shadow-xs lg:px-6 @xl/main:grid-cols-2 @5xl/main:grid-cols-4">
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>Total Revenue</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            $1,250.00
          </CardTitle>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            Trending up this month <TrendingUp className="size-4" />
          </div>
          <div className="text-muted-foreground">
            Visitors for the last 6 months
          </div>
        </CardFooter>
      </Card>
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>New Customers</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            1,234
          </CardTitle>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            Down 20% this period <TrendingDown className="size-4" />
          </div>
          <div className="text-muted-foreground">
            Acquisition needs attention
          </div>
        </CardFooter>
      </Card>
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>Active Accounts</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            45,678
          </CardTitle>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            Strong user retention <TrendingUp className="size-4" />
          </div>
          <div className="text-muted-foreground">Engagement exceed targets</div>
        </CardFooter>
      </Card>
      <Card className="@container/card">
        <CardHeader>
          <CardDescription>Growth Rate</CardDescription>
          <CardTitle className="text-2xl font-semibold tabular-nums @[250px]/card:text-3xl">
            4.5%
          </CardTitle>
        </CardHeader>
        <CardFooter className="flex-col items-start gap-1.5 text-sm">
          <div className="line-clamp-1 flex gap-2 font-medium">
            Steady performance increase <TrendingUp className="size-4" />
          </div>
          <div className="text-muted-foreground">Meets growth projections</div>
        </CardFooter>
      </Card>
    </div>
  )
}
