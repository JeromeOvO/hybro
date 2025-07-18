import { useState, useRef, useCallback } from 'react'
import { queryBaseTask } from '@/lib/api'
import type { BaseTask } from '@/lib/types'

interface PollingTask {
  taskId: string
  timeoutRef: NodeJS.Timeout
  intervalRef: NodeJS.Timeout | null
  startTime: number
}

export function useTaskPolling() {
  const [pollingTasks, setPollingTasks] = useState<Map<string, PollingTask>>(new Map())
  const completedTaskIds = useRef<Set<string>>(new Set())
  const pollingTasksRef = useRef<Map<string, PollingTask>>(new Map())

  // Sync ref and state
  pollingTasksRef.current = pollingTasks

  // Check if specific baseTask is complete
  const isBaseTaskComplete = useCallback((baseTask: BaseTask): boolean => {
    const userMessages = baseTask.task.history?.filter(msg => msg.role === 'user') || []
    const agentMessages = baseTask.task.history?.filter(msg => msg.role === 'agent') || []
    
    // When user and agent message counts are equal, it means the agent has replied to the user's last message
    return userMessages.length === agentMessages.length && agentMessages.length > 0
  }, [])

  // Check if baseTask needs polling (has user messages but no corresponding agent reply)
  const needsPolling = useCallback((baseTask: BaseTask): boolean => {
    const userMessages = baseTask.task.history?.filter(msg => msg.role === 'user') || []
    const agentMessages = baseTask.task.history?.filter(msg => msg.role === 'agent') || []
    
    // If user message count is greater than agent message count, polling is needed to wait for agent reply
    return userMessages.length > agentMessages.length
  }, [])

  // Stop polling specific task
  const stopPolling = useCallback((taskId: string) => {
    const task = pollingTasksRef.current.get(taskId)
    if (task) {
      clearTimeout(task.timeoutRef)
      if (task.intervalRef) {
        clearInterval(task.intervalRef)
      }
      setPollingTasks(prev => {
        const newMap = new Map(prev)
        newMap.delete(taskId)
        return newMap
      })
      console.log(`Stopped polling for task: ${taskId}`)
    }
  }, [])

  // Start polling specific task
  const startPolling = useCallback((
    taskId: string, 
    onUpdate: (baseTask: BaseTask) => void,
    onComplete: (baseTask: BaseTask) => void,
    onError: (error: string) => void
  ) => {
    // If already completed or currently polling, don't start again
    if (completedTaskIds.current.has(taskId) || pollingTasksRef.current.has(taskId)) {
      return
    }

    console.log(`Starting polling for task: ${taskId}`)

    // Delay polling start
    const delayTimeout = setTimeout(() => {
      const startTime = Date.now()
      const POLLING_INTERVAL = 5000 // 5 second polling interval
      const POLLING_TIMEOUT = 60000 // 60 second timeout

      const pollTask = async () => {
        try {
          const response = await queryBaseTask(taskId)
          const baseTask = response.base_task

          if (baseTask) {
            // Update UI
            onUpdate(baseTask)

            // Check if completed
            if (isBaseTaskComplete(baseTask)) {
              console.log(`Task ${taskId} completed`)
              completedTaskIds.current.add(taskId)
              stopPolling(taskId)
              onComplete(baseTask)
              return
            }

            // Check timeout
            if (Date.now() - startTime > POLLING_TIMEOUT) {
              console.log(`Task ${taskId} polling timeout`)
              stopPolling(taskId)
              onError('Agent response timeout. Please try again.')
              return
            }
          }
        } catch (error) {
          console.error(`Error polling task ${taskId}:`, error)
          stopPolling(taskId)
          onError('Failed to get agent response. Please try again.')
        }
      }

      // Execute immediately once
      pollTask()

      // Set up interval polling
      const intervalRef = setInterval(pollTask, POLLING_INTERVAL)

      // Set timeout
      const timeoutRef = setTimeout(() => {
        console.log(`Task ${taskId} polling timeout`)
        stopPolling(taskId)
        onError('Agent response timeout. Please try again.')
      }, POLLING_TIMEOUT)

      // Save polling task
      setPollingTasks(prev => new Map(prev).set(taskId, {
        taskId,
        timeoutRef,
        intervalRef,
        startTime
      }))
    }, 100) // 100ms delay start

    // Temporarily save delay timeout
    setPollingTasks(prev => new Map(prev).set(taskId, {
      taskId,
      timeoutRef: delayTimeout,
      intervalRef: null,
      startTime: Date.now()
    }))
  }, [isBaseTaskComplete, stopPolling])

  // Stop all polling
  const stopAllPolling = useCallback(() => {
    pollingTasksRef.current.forEach((task) => {
      clearTimeout(task.timeoutRef)
      if (task.intervalRef) {
        clearInterval(task.intervalRef)
      }
    })
    setPollingTasks(new Map())
    console.log('Stopped all polling')
  }, [])

  // Check if currently polling
  const isPolling = useCallback((taskId: string) => {
    return pollingTasksRef.current.has(taskId)
  }, [])

  // Get list of tasks currently being polled
  const getPollingTaskIds = useCallback(() => {
    return Array.from(pollingTasksRef.current.keys())
  }, [])

  return {
    startPolling,
    stopPolling,
    stopAllPolling,
    isPolling,
    getPollingTaskIds,
    needsPolling,
    isBaseTaskComplete
  }
} 