'use client'

import { useState, useEffect } from 'react'
import { Plus, Minus, Users } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Badge } from '@/components/ui/badge'
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar'
import { getAllAgents } from '@/lib/api/agent'
import type { Agent } from '@/lib/types/agent'

interface AgentSelectorProps {
  selectedAgents: { [agentId: string]: Agent }
  onAgentAdd: (agent: Agent) => void
  onAgentRemove: (agentId: string) => void
  className?: string
}

export function AgentSelector({
  selectedAgents,
  onAgentAdd,
  onAgentRemove,
  className
}: AgentSelectorProps) {
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // Load agents on component mount
  useEffect(() => {
    loadAvailableAgents()
  }, [])

  const loadAvailableAgents = async () => {
    try {
      setLoading(true)
      setError(null)
      const response = await getAllAgents()
      
      if (response.success && response.agents) {
        setAvailableAgents(response.agents)
      } else {
        throw new Error(response.error || 'Failed to load agents')
      }
    } catch (error) {
      console.error('Failed to load agents:', error)
      setError(error instanceof Error ? error.message : 'Failed to load agents')
    } finally {
      setLoading(false)
    }
  }

  const selectedAgentsList = Object.values(selectedAgents)
  const unselectedAgents = availableAgents.filter(
    agent => !selectedAgents[agent.agent_id]
  )

  return (
    <div className={`space-y-4 ${className}`}>
      {/* Header */}
      <div className="flex items-center gap-2">
        <Users className="w-5 h-5" />
        <Label className="text-base font-semibold">代理管理</Label>
        <Badge variant="secondary">
          已选择 {selectedAgentsList.length} 个代理
        </Badge>
      </div>

      {/* Error State */}
      {error && (
        <div className="p-3 rounded-lg border border-destructive/20 bg-destructive/10 text-destructive text-sm">
          {error}
          <Button
            size="sm"
            variant="ghost"
            onClick={loadAvailableAgents}
            className="ml-2 h-auto p-1 text-destructive hover:text-destructive"
          >
            重试
          </Button>
        </div>
      )}

      {/* Selected Agents */}
      {selectedAgentsList.length > 0 && (
        <div className="space-y-3">
          <h4 className="text-sm font-medium text-muted-foreground">已邀请的代理</h4>
          <div className="grid gap-3">
            {selectedAgentsList.map((agent) => (
              <div
                key={agent.agent_id}
                className="flex items-center justify-between p-3 rounded-lg border bg-muted/50"
              >
                <div className="flex items-center gap-3">
                  <Avatar className="w-8 h-8">
                    <AvatarImage src={agent.agent_card.iconUrl || undefined} />
                    <AvatarFallback>
                      {agent.agent_card.name.charAt(0).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-sm truncate">{agent.agent_card.name}</div>
                    <div className="text-xs text-muted-foreground line-clamp-1">
                      {agent.agent_card.description}
                    </div>
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onAgentRemove(agent.agent_id)}
                  className="text-destructive hover:text-destructive shrink-0"
                >
                  <Minus className="w-4 h-4" />
                </Button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Available Agents */}
      <div className="space-y-3">
        <h4 className="text-sm font-medium text-muted-foreground">可用的代理</h4>
        {loading ? (
          <div className="text-center py-4 text-muted-foreground">
            加载代理中...
          </div>
        ) : error ? (
          <div className="text-center py-4 text-muted-foreground">
            加载失败，请点击上方重试按钮
          </div>
        ) : unselectedAgents.length === 0 ? (
          <div className="text-center py-4 text-muted-foreground">
            {availableAgents.length === 0 ? '暂无可用代理' : '所有代理已被邀请'}
          </div>
        ) : (
          <div className="grid gap-3 max-h-60 overflow-y-auto">
            {unselectedAgents.map((agent) => (
              <div
                key={agent.agent_id}
                className="flex items-center justify-between p-3 rounded-lg border hover:bg-muted/30 transition-colors"
              >
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <Avatar className="w-8 h-8">
                    <AvatarImage src={agent.agent_card.iconUrl || undefined} />
                    <AvatarFallback>
                      {agent.agent_card.name.charAt(0).toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <div className="font-medium text-sm truncate">{agent.agent_card.name}</div>
                    <div className="text-xs text-muted-foreground line-clamp-1">
                      {agent.agent_card.description}
                    </div>
                    {agent.agent_card.skills && agent.agent_card.skills.length > 0 && (
                      <div className="flex gap-1 mt-1 flex-wrap">
                        {agent.agent_card.skills.slice(0, 2).map((skill) => (
                          <Badge key={skill.id} variant="outline" className="text-xs">
                            {skill.name}
                          </Badge>
                        ))}
                        {agent.agent_card.skills.length > 2 && (
                          <Badge variant="outline" className="text-xs">
                            +{agent.agent_card.skills.length - 2}
                          </Badge>
                        )}
                      </div>
                    )}
                  </div>
                </div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => onAgentAdd(agent)}
                  className="text-primary hover:text-primary shrink-0"
                >
                  <Plus className="w-4 h-4" />
                </Button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
