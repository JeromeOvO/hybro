"use client"

import { useEffect } from 'react'
import { WorkflowMessage } from './workflow-message'
import { useWorkflow } from '@/hooks/useWorkflow'
import type { BaseTask, MetaTask } from '@/lib/types'

interface WorkflowContainerProps {
  baseTaskId: string
  metaTasks?: MetaTask[]
  onWorkflowComplete?: (baseTask: BaseTask) => void
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