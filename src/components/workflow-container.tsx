"use client"

import { useEffect, useMemo } from 'react'
import { WorkflowMessage } from './workflow-message'
import { useWorkflow } from '@/hooks/useWorkflow'
import type { BaseTask } from '@/lib/types'

interface WorkflowContainerProps {
  baseTaskId: string
  metaTasks?: any[] // Initial meta tasks from API
  onWorkflowComplete?: (baseTask: BaseTask) => void // Modify callback parameter
  className?: string
}

export function WorkflowContainer({
  baseTaskId,
  metaTasks: initialMetaTasks = [],
  onWorkflowComplete,
  className
}: WorkflowContainerProps) {
  const {
    metaTasks,
    agents,
    stage,
    isLoading,
    setMetaTasks,
    handleNext,
    handleRetry,
    handleRetryMetaTask
  } = useWorkflow({ baseTaskId, onWorkflowComplete })

  // Use useMemo to stabilize onWorkflowComplete reference
  const stableOnWorkflowComplete = useMemo(() => onWorkflowComplete, [onWorkflowComplete])

  // If there are initial meta tasks, set them (but don't override hook's auto-initialization)
  useEffect(() => {
    if (initialMetaTasks.length > 0 && metaTasks.length === 0) {
      console.log('Setting initial meta tasks:', initialMetaTasks.length)
      setMetaTasks(initialMetaTasks)
    }
  }, [initialMetaTasks.length, metaTasks.length, setMetaTasks])

  return (
    <div className={className}>
      <WorkflowMessage
        baseTaskId={baseTaskId}
        metaTasks={metaTasks}
        agents={agents}
        stage={stage}
        isLoading={isLoading}
        onNext={handleNext}
        onRetry={handleRetry}
        onRetryMetaTask={handleRetryMetaTask}
      />
    </div>
  )
} 