import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { renderHook, act, cleanup, waitFor } from '@testing-library/react'

vi.mock('@/lib/api', () => ({
  decomposeTask: vi.fn(),
  assignAgentsToMetaTasks: vi.fn(),
  retryMetaTask: vi.fn(),
  runWorkflow: vi.fn(),
  summarizeMetaTaskForBaseTask: vi.fn(),
  getMetaTasksByParentId: vi.fn(),
  queryBaseTask: vi.fn(),
  getAllAgents: vi.fn(),
}))

vi.mock('@/components/ui/banner', () => ({
  banner: { info: vi.fn(), error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}))

import {
  decomposeTask,
  assignAgentsToMetaTasks,
  retryMetaTask,
  runWorkflow,
  summarizeMetaTaskForBaseTask,
  getMetaTasksByParentId,
  queryBaseTask,
  getAllAgents,
} from '@/lib/api'
import { banner } from '@/components/ui/banner'
import { useWorkflow } from '@/hooks/useWorkflow'
import { WORKFLOW_STAGE } from '@/components/workflow-message'

const mockGetMetaTasks = getMetaTasksByParentId as ReturnType<typeof vi.fn>
const mockGetAllAgents = getAllAgents as ReturnType<typeof vi.fn>
const mockDecompose = decomposeTask as ReturnType<typeof vi.fn>
const mockAssign = assignAgentsToMetaTasks as ReturnType<typeof vi.fn>
const mockRunWorkflow = runWorkflow as ReturnType<typeof vi.fn>
const mockSummarize = summarizeMetaTaskForBaseTask as ReturnType<typeof vi.fn>
const mockQueryBaseTask = queryBaseTask as ReturnType<typeof vi.fn>
const mockRetryMetaTask = retryMetaTask as ReturnType<typeof vi.fn>

const TASK_ID = 'base-task-1'

describe('useWorkflow', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetAllAgents.mockResolvedValue({ success: true, agents: [{ agent_id: 'a-1', agent_card: { name: 'Agent 1' } }] })
    mockGetMetaTasks.mockResolvedValue({ meta_tasks: [] })
    mockDecompose.mockResolvedValue({ success: true })
  })

  afterEach(() => {
    cleanup()
  })

  it('should initialize with default state', () => {
    mockGetMetaTasks.mockResolvedValue({ meta_tasks: [] })
    const { result } = renderHook(() => useWorkflow({ baseTaskId: '' }))

    expect(result.current.metaTasks).toEqual([])
    expect(result.current.agents).toEqual([])
    expect(result.current.stage).toBe(WORKFLOW_STAGE.DECOMPOSED)
    expect(result.current.isLoading).toBe(false)
  })

  it('should auto-initialize when baseTaskId is set', async () => {
    mockGetMetaTasks.mockResolvedValue({ meta_tasks: [] })

    renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

    await waitFor(() => {
      expect(mockGetAllAgents).toHaveBeenCalled()
      expect(mockGetMetaTasks).toHaveBeenCalledWith(TASK_ID)
    })
  })

  it('should auto-decompose when no existing meta tasks', async () => {
    mockGetMetaTasks.mockResolvedValue({ meta_tasks: [] })
    mockDecompose.mockResolvedValue({ success: true })

    renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

    await waitFor(() => {
      expect(mockDecompose).toHaveBeenCalledWith({ task_id: TASK_ID })
    })
  })

  it('should skip decompose when meta tasks already exist', async () => {
    const existingTasks = [{ task_id: 'mt-1', task_name: 'Sub task 1' }]
    mockGetMetaTasks.mockResolvedValue({ meta_tasks: existingTasks })

    const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

    await waitFor(() => {
      expect(mockDecompose).not.toHaveBeenCalled()
      expect(result.current.metaTasks).toEqual(existingTasks)
    })
  })

  it('should detect assigned agents stage from existing tasks', async () => {
    const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
    mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })

    const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

    await waitFor(() => {
      expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED)
    })
  })

  describe('handleNext', () => {
    it('should assign agents when stage is DECOMPOSED', async () => {
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: [{ task_id: 'mt-1' }] })
      mockAssign.mockResolvedValue({ success: true })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(result.current.metaTasks.length).toBe(1))

      await act(async () => {
        await result.current.handleNext()
      })

      expect(mockAssign).toHaveBeenCalledWith({ task_id: TASK_ID })
      expect(banner.success).toHaveBeenCalledWith('Agents assigned successfully')
    })

    it('should run workflow when stage is AGENTS_ASSIGNED', async () => {
      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockRunWorkflow.mockResolvedValue({ success: true })
      mockSummarize.mockResolvedValue({ success: true })
      mockQueryBaseTask.mockResolvedValue({ base_task: { task_id: TASK_ID, summary: 'Done' } })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      await act(async () => {
        await result.current.handleNext()
      })

      expect(mockRunWorkflow).toHaveBeenCalledWith({ task_id: TASK_ID })
    })

    it('should call summarize when stage is RUNNING (line 206)', async () => {
      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockRunWorkflow.mockResolvedValue({ success: true })
      // First summarize (called by handleRunWorkflow) succeeds and sets COMPLETED
      mockSummarize.mockResolvedValue({ success: true })
      mockQueryBaseTask.mockResolvedValue({ base_task: { task_id: TASK_ID } })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      // Trigger handleRunWorkflow which sets RUNNING, then calls summarize
      // But summarize immediately sets COMPLETED. Instead, make runWorkflow set RUNNING
      // then fail summarize so we stay at RUNNING, then call handleNext again.
      mockSummarize.mockRejectedValueOnce(new Error('temp fail'))

      await act(async () => {
        await result.current.handleNext() // AGENTS_ASSIGNED → handleRunWorkflow → RUNNING
      })

      // Stage should be RUNNING because runWorkflow succeeded but summarize threw
      // Actually handleRunWorkflow catches internally, but stage was set to RUNNING before summarize
      // Let's verify and call handleNext at RUNNING stage
      mockSummarize.mockResolvedValue({ success: true })
      mockQueryBaseTask.mockResolvedValue({ base_task: { task_id: TASK_ID } })

      await act(async () => {
        await result.current.handleNext() // RUNNING → handleSummarizeResults
      })

      // summarize should have been called again via handleNext → RUNNING branch
      expect(mockSummarize).toHaveBeenCalledTimes(2)
    })
  })

  describe('handleRetry', () => {
    it('should re-decompose when stage is DECOMPOSED', async () => {
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: [] })
      mockDecompose.mockResolvedValue({ success: true })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(mockGetAllAgents).toHaveBeenCalled())

      await act(async () => {
        await result.current.handleRetry()
      })

      expect(mockDecompose).toHaveBeenCalled()
    })

    it('should re-assign agents when stage is AGENTS_ASSIGNED (line 221)', async () => {
      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockAssign.mockResolvedValue({ success: true })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      await act(async () => {
        await result.current.handleRetry()
      })

      expect(mockAssign).toHaveBeenCalledWith({ task_id: TASK_ID })
    })

    it('should re-run workflow when stage is RUNNING (line 224)', async () => {
      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockRunWorkflow.mockResolvedValue({ success: true })
      // Let first summarize fail so stage stays at RUNNING
      mockSummarize.mockRejectedValueOnce(new Error('temp'))

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))
      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      // Move to RUNNING via handleNext
      await act(async () => {
        await result.current.handleNext()
      })

      // Now retry at RUNNING should call handleRunWorkflow
      mockRunWorkflow.mockResolvedValue({ success: true })
      mockSummarize.mockResolvedValue({ success: true })
      mockQueryBaseTask.mockResolvedValue({ base_task: { task_id: TASK_ID } })

      await act(async () => {
        await result.current.handleRetry()
      })

      expect(mockRunWorkflow).toHaveBeenCalledTimes(2)
    })

    it('should re-summarize when stage is COMPLETED (line 227)', async () => {
      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockRunWorkflow.mockResolvedValue({ success: true })
      mockSummarize.mockResolvedValue({ success: true })
      mockQueryBaseTask.mockResolvedValue({ base_task: { task_id: TASK_ID } })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))
      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      // Run workflow to reach COMPLETED
      await act(async () => {
        await result.current.handleNext()
      })

      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.COMPLETED))

      const callsBefore = mockSummarize.mock.calls.length

      // Retry at COMPLETED calls handleSummarizeResults
      await act(async () => {
        await result.current.handleRetry()
      })

      expect(mockSummarize).toHaveBeenCalledTimes(callsBefore + 1)
    })
  })

  describe('handleRetryMetaTask', () => {
    it('should retry specific meta task and refresh', async () => {
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: [{ task_id: 'mt-1' }] })
      mockRetryMetaTask.mockResolvedValue({ success: true })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(result.current.metaTasks.length).toBe(1))

      await act(async () => {
        await result.current.handleRetryMetaTask('mt-1')
      })

      expect(mockRetryMetaTask).toHaveBeenCalledWith({ task_id: 'mt-1' })
      expect(banner.success).toHaveBeenCalledWith('Meta task retried successfully')
    })

    it('should show error when retry fails', async () => {
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: [{ task_id: 'mt-1' }] })
      mockRetryMetaTask.mockRejectedValue(new Error('Network error'))

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(result.current.metaTasks.length).toBe(1))

      await act(async () => {
        await result.current.handleRetryMetaTask('mt-1')
      })

      expect(banner.error).toHaveBeenCalledWith('Failed to retry meta task')
    })
  })

  describe('error handling', () => {
    it('should show error when decompose fails', async () => {
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: [] })
      mockDecompose.mockRejectedValue(new Error('Server error'))

      renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => {
        expect(banner.error).toHaveBeenCalledWith('Failed to decompose task')
      })
    })

    it('should show error when assign agents fails', async () => {
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: [{ task_id: 'mt-1' }] })
      mockAssign.mockRejectedValue(new Error('Fail'))

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(result.current.metaTasks.length).toBe(1))

      await act(async () => {
        await result.current.handleNext()
      })

      expect(banner.error).toHaveBeenCalledWith('Failed to assign agents')
    })

    it('should handle unsuccessful API responses', async () => {
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: [{ task_id: 'mt-1' }] })
      mockAssign.mockResolvedValue({ success: false, error: 'No suitable agents' })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => expect(result.current.metaTasks.length).toBe(1))

      await act(async () => {
        await result.current.handleNext()
      })

      expect(banner.error).toHaveBeenCalledWith('Failed to assign agents')
    })

    it('should show error when summarize returns success=false (line 146)', async () => {
      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockRunWorkflow.mockResolvedValue({ success: true })
      mockSummarize.mockResolvedValue({ success: false, error: 'Summarization failed' })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))
      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      await act(async () => {
        await result.current.handleNext()
      })

      expect(banner.error).toHaveBeenCalledWith('Failed to summarize results')
    })

    it('should show error when runWorkflow returns success=false (line 185)', async () => {
      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockRunWorkflow.mockResolvedValue({ success: false, error: 'Execution timeout' })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))
      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      await act(async () => {
        await result.current.handleNext()
      })

      expect(banner.error).toHaveBeenCalledWith('Failed to start workflow')
    })

    it('should show error when runWorkflow throws (network error)', async () => {
      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockRunWorkflow.mockRejectedValue(new Error('Network timeout'))

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))
      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      await act(async () => {
        await result.current.handleNext()
      })

      expect(banner.error).toHaveBeenCalledWith('Failed to start workflow')
      expect(result.current.isLoading).toBe(false)
    })

    it('should handle init error when loadAgents throws (line 293)', async () => {
      // To hit the outer initializeWorkflow catch (L292-296), we need an
      // error to escape one of the inner callbacks' own try/catch. We achieve
      // this by making getAllAgents reject, which enters loadAgents' catch,
      // and then making banner.error itself throw — the re-thrown error
      // propagates to the outer catch.
      mockGetAllAgents.mockRejectedValue(new Error('Agent service down'))
      const bannerError = banner.error as ReturnType<typeof vi.fn>
      bannerError.mockImplementationOnce(() => {
        throw new Error('banner.error also crashed')
      })

      const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {})

      renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))

      await waitFor(() => {
        expect(consoleSpy).toHaveBeenCalledWith(
          'Error initializing workflow:',
          expect.any(Error)
        )
      })

      consoleSpy.mockRestore()
    })

    it('should handle retryMetaTask returning success=false (line 245)', async () => {
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: [{ task_id: 'mt-1' }] })
      mockRetryMetaTask.mockResolvedValue({ success: false, error: 'Agent busy' })

      const { result } = renderHook(() => useWorkflow({ baseTaskId: TASK_ID }))
      await waitFor(() => expect(result.current.metaTasks.length).toBe(1))

      await act(async () => {
        await result.current.handleRetryMetaTask('mt-1')
      })

      expect(banner.error).toHaveBeenCalledWith('Failed to retry meta task')
    })
  })

  describe('workflow completion', () => {
    it('should call onWorkflowComplete when summarize succeeds', async () => {
      const onComplete = vi.fn()
      const baseTask = { task_id: TASK_ID, summary: 'All done' }

      const tasks = [{ task_id: 'mt-1', agent_id: 'a-1' }]
      mockGetMetaTasks.mockResolvedValue({ meta_tasks: tasks })
      mockRunWorkflow.mockResolvedValue({ success: true })
      mockSummarize.mockResolvedValue({ success: true })
      mockQueryBaseTask.mockResolvedValue({ base_task: baseTask })

      const { result } = renderHook(() =>
        useWorkflow({ baseTaskId: TASK_ID, onWorkflowComplete: onComplete })
      )

      await waitFor(() => expect(result.current.stage).toBe(WORKFLOW_STAGE.AGENTS_ASSIGNED))

      await act(async () => {
        await result.current.handleNext()
      })

      await waitFor(() => {
        expect(onComplete).toHaveBeenCalledWith(baseTask)
        expect(banner.success).toHaveBeenCalledWith('Workflow completed successfully')
      })
    })
  })
})
