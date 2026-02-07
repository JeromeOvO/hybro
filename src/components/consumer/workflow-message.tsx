"use client"

import { useState } from "react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Button } from "@/components/ui/button"
import { Badge } from "@/components/ui/badge"
import { Separator } from "@/components/ui/separator"
import { 
  ChevronRight, 
  RefreshCw, 
  Bot, 
  CheckCircle, 
  AlertCircle,
  Loader2,
  Play,
  RotateCcw
} from "lucide-react"
import type { MetaTask, Agent } from "@/lib/types"
import { cn } from "@/lib/utils"

export type WorkflowStage = 'decomposed' | 'agents_assigned' | 'running' | 'completed'

interface WorkflowMessageProps {
  baseTaskId: string
  metaTasks: MetaTask[]
  agents?: Agent[]
  stage: WorkflowStage
  isLoading?: boolean
  onNext: () => void
  onRetry: () => void
  onRetryMetaTask?: (metaTaskId: string) => void
  className?: string
}

export function WorkflowMessage({
  metaTasks,
  agents = [],
  stage,
  isLoading = false,
  onNext,
  onRetry,
  onRetryMetaTask,
  className
}: WorkflowMessageProps) {
  const [expandedTasks, setExpandedTasks] = useState<Set<string>>(new Set())

  const toggleTaskExpansion = (taskId: string) => {
    const newExpanded = new Set(expandedTasks)
    if (newExpanded.has(taskId)) {
      newExpanded.delete(taskId)
    } else {
      newExpanded.add(taskId)
    }
    setExpandedTasks(newExpanded)
  }

  const getAgentById = (agentId?: string) => {
    if (!agentId) return null
    return agents.find(agent => agent.agent_id === agentId)
  }

  const getStageInfo = () => {
    switch (stage) {
      case 'decomposed':
        return {
          title: 'Task Decomposition Complete',
          description: 'The task has been broken down into manageable sub-tasks',
          nextLabel: 'Assign Agents',
          icon: <CheckCircle className="h-5 w-5 text-green-500" />
        }
      case 'agents_assigned':
        return {
          title: 'Agents Assigned',
          description: 'All meta-tasks have been assigned to appropriate agents',
          nextLabel: 'Run Workflow',
          icon: <Bot className="h-5 w-5 text-blue-500" />
        }
      case 'running':
        return {
          title: 'Workflow Running',
          description: 'The workflow is currently being executed',
          nextLabel: 'Running...',
          icon: <Loader2 className="h-5 w-5 text-orange-500 animate-spin" />
        }
      case 'completed':
        return {
          title: 'Workflow Completed',
          description: 'All tasks have been executed successfully',
          nextLabel: 'Completed',
          icon: <CheckCircle className="h-5 w-5 text-green-500" />
        }
    }
  }

  const stageInfo = getStageInfo()
  const sortedMetaTasks = [...metaTasks].sort((a, b) => (a.execution_order || 0) - (b.execution_order || 0))

  return (
    <Card className={cn("bg-transparent border-0 shadow-none rounded-none p-0", className)}>
      <CardHeader className="pb-4">
        <div className="flex items-center gap-3">
          {stageInfo.icon}
          <div className="flex-1 min-w-0"> {/* Add min-w-0 to prevent overflow */}
            <CardTitle className="text-lg">{stageInfo.title}</CardTitle>
            <CardDescription className="break-words">{stageInfo.description}</CardDescription>
          </div>
          <Badge variant="outline" className="text-xs flex-shrink-0">
            {metaTasks.length} tasks
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="space-y-4">
        {/* Meta Tasks List */}
        <div className="space-y-3">
          <h4 className="text-sm font-medium text-muted-foreground">Sub-tasks</h4>
          {sortedMetaTasks.map((metaTask, index) => {
            const agent = getAgentById(metaTask.agent_id)
            const isExpanded = expandedTasks.has(metaTask.task_id)
            
            return (
              <Card key={metaTask.task_id} className="bg-transparent border-0 shadow-none rounded-none">
                <CardHeader 
                  className="pb-3 cursor-pointer hover:bg-muted/30 transition-colors"
                  onClick={() => toggleTaskExpansion(metaTask.task_id)}
                >
                  <div className="flex items-center gap-3">
                    <div className="flex items-center justify-center w-6 h-6 rounded-full bg-primary/10 text-primary text-xs font-medium flex-shrink-0">
                      {metaTask.execution_order || index + 1}
                    </div>
                    <div className="flex-1 min-w-0"> {/* Add min-w-0 to prevent overflow */}
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-medium break-words"> {/* Add break-words */}
                          {metaTask.task_description || `Meta Task ${index + 1}`}
                        </span>
                        <ChevronRight className={cn(
                          "h-4 w-4 text-muted-foreground transition-transform flex-shrink-0",
                          isExpanded && "rotate-90"
                        )} />
                      </div>
                      {agent && (
                        <div className="flex items-center gap-2 mt-1">
                          <Bot className="h-3 w-3 text-muted-foreground flex-shrink-0" />
                          <span className="text-xs text-muted-foreground break-words">
                            {agent.agent_card.name}
                          </span>
                        </div>
                      )}
                    </div>
                    
                    {stage === 'agents_assigned' && onRetryMetaTask && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={(e) => {
                          e.stopPropagation()
                          onRetryMetaTask(metaTask.task_id)
                        }}
                        className="h-7 px-2 flex-shrink-0"
                      >
                        <RotateCcw className="h-3 w-3" />
                      </Button>
                    )}
                  </div>
                </CardHeader>
                
                {isExpanded && (
                  <CardContent className="pt-0">
                    <div className="space-y-3 text-sm">
                      <div>
                        <span className="font-medium">Task ID:</span>
                        <span className="ml-2 text-muted-foreground font-mono text-xs break-all">
                          {metaTask.task_id}
                        </span>
                      </div>
                      
                      {metaTask.task_description && (
                        <div>
                          <span className="font-medium">Description:</span>
                          <p className="mt-1 text-muted-foreground break-words whitespace-pre-wrap">
                            {metaTask.task_description}
                          </p>
                        </div>
                      )}
                      
                      {agent && (
                        <div>
                          <span className="font-medium">Assigned Agent:</span>
                          <div className="mt-1 p-2 bg-muted/30 rounded">
                            <div className="font-medium text-xs break-words">{agent.agent_card.name}</div>
                            <div className="text-xs text-muted-foreground mt-1 break-words">
                              {agent.agent_card.description}
                            </div>
                            {agent.agent_card.provider && (
                              <div className="text-xs text-muted-foreground mt-1 break-words">
                                Provider: {agent.agent_card.provider.organization}
                              </div>
                            )}
                          </div>
                        </div>
                      )}
                      
                      {!agent && stage === 'agents_assigned' && (
                        <div className="flex items-center gap-2 text-orange-600">
                          <AlertCircle className="h-4 w-4 flex-shrink-0" />
                          <span className="text-sm">No agent assigned</span>
                        </div>
                      )}
                    </div>
                  </CardContent>
                )}
              </Card>
            )
          })}
        </div>

        <Separator />

        {/* Action Buttons */}
        <div className="flex items-center justify-between">
          <Button
            variant="outline"
            onClick={onRetry}
            disabled={isLoading || stage === 'running'}
            className="flex items-center gap-2"
          >
            <RefreshCw className={cn("h-4 w-4", isLoading && "animate-spin")} />
            Retry
          </Button>

          <Button
            onClick={onNext}
            disabled={isLoading || stage === 'running' || stage === 'completed'}
            className="flex items-center gap-2"
          >
            {stage === 'running' ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {isLoading ? 'Processing...' : stageInfo.nextLabel}
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}
