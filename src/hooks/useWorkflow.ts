import { useState, useCallback, useEffect, useRef } from 'react'
import { 
  decomposeTask, 
  assignAgentsToMetaTasks, 
  retryMetaTask,
  runWorkflow,
  summarizeMetaTaskForBaseTask,
  getMetaTasksByParentId,
  queryBaseTask
} from '@/lib/api'
import { getAllAgents } from '@/lib/api'
import { toast } from 'sonner'
import type { MetaTask, Agent, OrchestrationCenterResponse, AgentCenterResponse, BaseTask } from '@/lib/types'
import type { WorkflowStage } from '@/components/workflow-message'

interface UseWorkflowProps {
  baseTaskId: string
  onWorkflowComplete?: (baseTask: BaseTask) => void
  getToken?: () => Promise<string | null>
}

export function useWorkflow({ baseTaskId, onWorkflowComplete, getToken }: UseWorkflowProps) {
  const [metaTasks, setMetaTasks] = useState<MetaTask[]>([])
  const [agents, setAgents] = useState<Agent[]>([])
  const [stage, setStage] = useState<WorkflowStage>('decomposed')
  const [isLoading, setIsLoading] = useState(false)
  
  // Use useRef to track initialization state to avoid duplicate initialization
  const initializationRef = useRef<{
    initialized: boolean
    initializing: boolean
    baseTaskId: string | null
  }>({
    initialized: false,
    initializing: false,
    baseTaskId: null
  })



  // Load all available agents
  const loadAgents = useCallback(async () => {
    try {
      const response: AgentCenterResponse = await getAllAgents(getToken)
      if (response.success && response.agents) {
        setAgents(response.agents)
      }
    } catch (error) {
      console.error('Error loading agents:', error)
      toast.error('Failed to load agents')
    }
  }, [getToken])

  // Load meta tasks by parent task ID
  const loadMetaTasks = useCallback(async () => {
    try {
      const response = await getMetaTasksByParentId(baseTaskId)
      const tasks = response.meta_tasks || []
      setMetaTasks(tasks)
      
      // Determine initial stage based on whether there are meta tasks
      if (tasks.length > 0) {
        // Check if agents are assigned
        const hasAssignedAgents = tasks.some(task => task.agent_id)
        if (hasAssignedAgents) {
          setStage('agents_assigned')
        } else {
          setStage('decomposed')
        }
      }
      
      return tasks
    } catch (error) {
      console.error('Error loading meta tasks:', error)
      return []
    }
  }, [baseTaskId])

  // Decompose task into meta tasks
  const handleDecomposeTask = useCallback(async () => {
    setIsLoading(true)
    try {
      console.log('Decomposing task:', baseTaskId)
      const response: OrchestrationCenterResponse = await decomposeTask({ task_id: baseTaskId })
      
      if (response.success) {
        // Load meta tasks after decomposition
        await loadMetaTasks()
        setStage('decomposed')
        toast.success('Task decomposed successfully')
      } else {
        throw new Error(response.error || 'Failed to decompose task')
      }
    } catch (error) {
      console.error('Error decomposing task:', error)
      toast.error('Failed to decompose task')
    } finally {
      setIsLoading(false)
    }
  }, [baseTaskId, loadMetaTasks])

  // Assign agents to all meta tasks
  const handleAssignAgents = useCallback(async () => {
    setIsLoading(true)
    try {
      const response: OrchestrationCenterResponse = await assignAgentsToMetaTasks({ 
        task_id: baseTaskId 
      })
      
      if (response.success) {
        // Refresh meta tasks to get assigned agents
        await loadMetaTasks()
        setStage('agents_assigned')
        toast.success('Agents assigned successfully')
      } else {
        throw new Error(response.error || 'Failed to assign agents')
      }
    } catch (error) {
      console.error('Error assigning agents:', error)
      toast.error('Failed to assign agents')
    } finally {
      setIsLoading(false)
    }
  }, [baseTaskId, loadMetaTasks])

    // Summarize results
    const handleSummarizeResults = useCallback(async () => {
        setIsLoading(true)
        try {
          const response: OrchestrationCenterResponse = await summarizeMetaTaskForBaseTask({ 
            task_id: baseTaskId 
          })
          
          if (response.success) {
            setStage('completed')
            toast.success('Workflow completed successfully')
            
            // Get updated baseTask
            const baseTaskResponse = await queryBaseTask(baseTaskId)
            const baseTask = baseTaskResponse.base_task
            
            if (baseTask && onWorkflowComplete) {
              onWorkflowComplete(baseTask)
            }
          } else {
            throw new Error(response.error || 'Failed to summarize results')
          }
        } catch (error) {
          console.error('Error summarizing results:', error)
          toast.error('Failed to summarize results')
        } finally {
          setIsLoading(false)
        }
      }, [baseTaskId, onWorkflowComplete])

  // Run workflow
  const handleRunWorkflow = useCallback(async () => {
    setIsLoading(true)
    try {
      const response: OrchestrationCenterResponse = await runWorkflow({
        task_id: baseTaskId
      })

      if (response.success) {
        // First set stage to running to ensure UI shows execution state
        setStage('running')
        toast.success('Workflow finished running, summarizing…')

        // ★ Key modification: call summarize directly instead of polling
        await handleSummarizeResults()
        return                                // <-- End function immediately

        // --- The following old polling code is no longer needed, entire section deleted ---
        // const pollInterval = setInterval(async () => {
        //   const isCompleted = await pollWorkflowStatus()
        //   if (isCompleted) {
        //     clearInterval(pollInterval)
        //   }
        // }, 3000)
        // setTimeout(() => {
        //   clearInterval(pollInterval)
        //   console.log('Workflow polling timeout')
        // }, 60000)
      } else {
        throw new Error(response.error || 'Failed to start workflow')
      }
    } catch (error) {
      console.error('Error running workflow:', error)
      toast.error('Failed to start workflow')
    } finally {
      setIsLoading(false)
    }
  }, [baseTaskId, handleSummarizeResults])     // ← Dependencies updated synchronously


  // Handle next action based on current stage
  const handleNext = useCallback(async () => {
    switch (stage) {
      case 'decomposed':
        await handleAssignAgents()
        break
      case 'agents_assigned':
        await handleRunWorkflow()
        break
      case 'running':
        await handleSummarizeResults()
        break
      case 'completed':
        // Already completed
        break
    }
  }, [stage, handleAssignAgents, handleRunWorkflow, handleSummarizeResults])

  // Retry workflow from current stage
  const handleRetry = useCallback(async () => {
    switch (stage) {
      case 'decomposed':
        await handleDecomposeTask()
        break
      case 'agents_assigned':
        await handleAssignAgents()
        break
      case 'running':
        await handleRunWorkflow()
        break
      case 'completed':
        await handleSummarizeResults()
        break
    }
  }, [stage, handleDecomposeTask, handleAssignAgents, handleRunWorkflow, handleSummarizeResults])

  // Retry specific meta task
  const handleRetryMetaTask = useCallback(async (metaTaskId: string) => {
    setIsLoading(true)
    try {
      const response: OrchestrationCenterResponse = await retryMetaTask({ 
        task_id: metaTaskId 
      })
      
      if (response.success) {
        // Refresh meta tasks to get updated assignment
        await loadMetaTasks()
        toast.success('Meta task retried successfully')
      } else {
        throw new Error(response.error || 'Failed to retry meta task')
      }
    } catch (error) {
      console.error('Error retrying meta task:', error)
      toast.error('Failed to retry meta task')
    } finally {
      setIsLoading(false)
    }
  }, [loadMetaTasks])

  // Initialize workflow - automatically decompose task
  useEffect(() => {
    const initRef = initializationRef.current
    
    // Check if initialization is needed
    if (!baseTaskId || 
        initRef.initialized || 
        initRef.initializing || 
        initRef.baseTaskId === baseTaskId) {
      return
    }

    // Mark initialization start
    initRef.initializing = true
    initRef.baseTaskId = baseTaskId

    const initializeWorkflow = async () => {
      try {
        console.log('Initializing workflow for baseTaskId:', baseTaskId)
        
        // Load agents first
        await loadAgents()
        
        // Check if meta tasks already exist
        const existingTasks = await loadMetaTasks()
        
        // If no meta tasks, automatically decompose task
        if (existingTasks.length === 0) {
          console.log('No existing meta tasks, decomposing task...')
          await handleDecomposeTask()
        } else {
          console.log('Found existing meta tasks:', existingTasks.length)
        }
        
        // Mark initialization complete
        initRef.initialized = true
        initRef.initializing = false
      } catch (error) {
        console.error('Error initializing workflow:', error)
        initRef.initialized = true
        initRef.initializing = false
      }
    }

    initializeWorkflow()
  }, [baseTaskId])

  // Reset initialization state when baseTaskId changes
  useEffect(() => {
    const initRef = initializationRef.current
    if (initRef.baseTaskId !== baseTaskId) {
      initRef.initialized = false
      initRef.initializing = false
      initRef.baseTaskId = null
    }
  }, [baseTaskId])

  return {
    metaTasks,
    agents,
    stage,
    isLoading,
    setMetaTasks,
    loadAgents,
    loadMetaTasks,
    handleNext,
    handleRetry,
    handleRetryMetaTask
  }
}