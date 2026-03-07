import Link from "next/link"
import { Bot, ChevronRight } from "lucide-react"
import { Card } from "@/components/ui/card"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"
import { deduplicateIcons } from "@/lib/agent-icon-utils"
import type { Agent } from "@/lib/types"

function getSkillLabel(skill: { name: string; tags: string[] }): string | null {
  const trimmed = skill.name?.trim()
  if (trimmed) return trimmed
  if (skill.tags?.length > 0) return skill.tags[0]
  return null
}

interface ConsumerAgentCardProps {
  agent: Agent
}

export function ConsumerAgentCard({ agent }: ConsumerAgentCardProps) {
  const isActive = agent.agent_status === "active"

  const allModes = [
    ...(agent.agent_card.defaultInputModes ?? []),
    ...(agent.agent_card.defaultOutputModes ?? []),
  ]
  const modeIcons = deduplicateIcons(allModes).slice(0, 3)

  const displayableSkills = agent.agent_card.skills
    .map(getSkillLabel)
    .filter((label): label is string => label !== null)
  const visibleSkills = displayableSkills.slice(0, 2)
  const overflowCount = displayableSkills.length - visibleSkills.length

  const provider = agent.agent_card.provider?.organization

  return (
    <Link
      href={`/c/agents/${agent.agent_id}`}
      className="block rounded-xl focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:outline-none"
    >
      <Card
        className={cn(
          "group relative h-full flex flex-col items-center p-5 gap-0",
          "border shadow-sm transition-all duration-300",
          "hover:-translate-y-0.5",
          "hover:shadow-[0_8px_40px_-12px_rgba(var(--color-primary)/0.25)]",
          "hover:border-primary/40",
          "dark:hover:shadow-[0_8px_50px_-10px_rgba(0,255,255,0.15)]",
          isActive
            ? "border-border bg-card"
            : "border-muted bg-muted/20",
        )}
      >
        <Badge
          variant={isActive ? "success" : "inactive"}
          className="absolute top-3 right-3"
        >
          {isActive ? "Active" : "Inactive"}
        </Badge>

        <div className="relative mt-2">
          <Avatar
            className={cn(
              "h-[62px] w-[62px] rounded-lg transition-shadow duration-300",
              "group-hover:shadow-[0_0_20px_-4px_rgba(var(--color-primary)/0.35)]",
              "dark:group-hover:shadow-[0_0_24px_-4px_rgba(0,255,255,0.25)]",
              !isActive && "grayscale",
            )}
          >
            <AvatarImage
              src={agent.agent_card.iconUrl || undefined}
              alt={agent.agent_card.name}
              className="rounded-lg"
            />
            <AvatarFallback className="rounded-lg">
              <Bot className="h-7 w-7" />
            </AvatarFallback>
          </Avatar>
          <span
            className={cn(
              "absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full border-[1.5px] border-background",
              isActive ? "bg-green-500" : "bg-muted-foreground/30",
            )}
          />
        </div>

        <p className="mt-3 text-base font-semibold text-center truncate w-full leading-tight">
          {agent.agent_card.name}
        </p>

        <p className="text-xs text-muted-foreground text-center min-h-[1rem] leading-normal">
          {provider || "\u00A0"}
        </p>

        {agent.agent_card.description ? (
          <p className="mt-2 text-sm text-muted-foreground text-center line-clamp-2 w-full min-h-[2.5rem]">
            {agent.agent_card.description}
          </p>
        ) : (
          <div className="mt-2 min-h-[2.5rem]" />
        )}

        {visibleSkills.length > 0 && (
          <div className="flex flex-wrap justify-center gap-1.5 mt-3">
            {visibleSkills.map((label) => (
              <Badge key={label} variant="badgeMuted">
                {label}
              </Badge>
            ))}
            {overflowCount > 0 && (
              <Badge variant="badgeMuted">+{overflowCount}</Badge>
            )}
          </div>
        )}

        {modeIcons.length > 0 && (
          <div className="flex items-center justify-center gap-1.5 mt-3">
            {modeIcons.map((Icon, i) => (
              <Icon key={i} className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            ))}
          </div>
        )}

        <div className="mt-auto pt-4">
          <span
            className={cn(
              "inline-flex items-center gap-1.5 px-4 py-2 rounded-full text-sm font-medium transition-colors",
              isActive
                ? "bg-primary text-primary-foreground"
                : "bg-muted text-muted-foreground",
            )}
          >
            View Agent
            <ChevronRight className="h-4 w-4" />
          </span>
        </div>
      </Card>
    </Link>
  )
}

export function ConsumerAgentCardSkeleton() {
  return (
    <Card className="h-full p-5 flex flex-col items-center gap-3">
      <Skeleton className="h-[62px] w-[62px] rounded-lg" />
      <Skeleton className="h-5 w-2/3" />
      <Skeleton className="h-3 w-1/3" />
      <div className="w-full space-y-1.5 mt-1">
        <Skeleton className="h-4 w-full" />
        <Skeleton className="h-4 w-4/5 mx-auto" />
      </div>
      <div className="flex gap-2 mt-1">
        <Skeleton className="h-5 w-16 rounded-md" />
        <Skeleton className="h-5 w-20 rounded-md" />
      </div>
      <div className="mt-auto">
        <Skeleton className="h-9 w-32 rounded-full" />
      </div>
    </Card>
  )
}
