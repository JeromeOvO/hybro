import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatRoomCreation } from "@/hooks/useChatRoomCreation"
import { useRoomUiStore } from "@/stores/room-ui-store"
import type { UseCaseTemplate } from "@/lib/use-case-templates"
import type { Agent } from "@/lib/types/agent"
import { Youtube } from "lucide-react"

// Mock next/navigation
const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}))

// Mock createNewRoom API
const mockCreateNewRoom = vi.fn()
vi.mock("@/lib/api/room", () => ({
  createNewRoom: (...args: unknown[]) => mockCreateNewRoom(...args),
}))

// Mock banner
vi.mock("@/components/ui/banner", () => ({
  banner: { error: vi.fn(), success: vi.fn() },
}))

function makeAgent(id: string, name: string): Agent {
  return {
    agent_id: id,
    agent_card: {
      name,
      description: "",
      url: `https://example.com/${id}`,
      version: "1.0.0",
      provider: { organization: "test", url: "https://test.com" },
      capabilities: {},
      protocolVersion: "1.0.0",
      skills: [],
      defaultInputModes: ["text"],
      defaultOutputModes: ["text"],
    },
  }
}

const catalog: Agent[] = [
  makeAgent("agent-001", "YouTube Creator Finder Agent"),
  makeAgent("agent-002", "GPT-5-mini Agent"),
]

const template: UseCaseTemplate = {
  id: "test-template",
  icon: Youtube,
  iconGradient: ["#ff0050", "#ff4080"],
  title: "Test Template Room",
  description: "A test template",
  agents: [
    { agentId: "agent-001", agentName: "YouTube Creator Finder Agent" },
    { agentId: "agent-002", agentName: "GPT-5-mini Agent" },
  ],
  prefillMessage: "Find top AI YouTubers",
  tag: "new",
}

describe("useChatRoomCreation.createFromTemplate", () => {
  const mockOnRequireAuth = vi.fn()

  beforeEach(() => {
    vi.clearAllMocks()
    useRoomUiStore.setState({ pendingRoomData: {} })
    mockCreateNewRoom.mockResolvedValue({
      success: true,
      room: { room_id: "new-room-123" },
    })
  })

  it("calls onRequireAuth and returns false when userId is missing", async () => {
    const { result } = renderHook(() =>
      useChatRoomCreation({
        userId: undefined,
        userName: undefined,
        getToken: vi.fn(),
        onRequireAuth: mockOnRequireAuth,
      }),
    )

    let success: boolean | undefined
    await act(async () => {
      success = await result.current.createFromTemplate(template, catalog)
    })

    expect(success).toBe(false)
    expect(mockOnRequireAuth).toHaveBeenCalledOnce()
    expect(mockCreateNewRoom).not.toHaveBeenCalled()
  })

  it("creates room with correct params and stores prefill handoff", async () => {
    const { result } = renderHook(() =>
      useChatRoomCreation({
        userId: "user-1",
        userName: "Test User",
        getToken: vi.fn(),
        onRequireAuth: mockOnRequireAuth,
      }),
    )

    let success: boolean | undefined
    await act(async () => {
      success = await result.current.createFromTemplate(template, catalog)
    })

    expect(success).toBe(true)

    // Verify createNewRoom was called with correct args
    expect(mockCreateNewRoom).toHaveBeenCalledOnce()
    const args = mockCreateNewRoom.mock.calls[0]
    expect(args[0]).toBe("Test Template Room") // room_name
    expect(args[1]).toBe("user-1") // room_owner_id
    expect(args[2]).toBe("Test User") // room_owner_name
    // args[4] = room_agent_set (legacy)
    expect(args[4]).toEqual({
      "agent-001": "YouTube Creator Finder Agent",
      "agent-002": "GPT-5-mini Agent",
    })
    // args[5] = extend_info
    expect(args[5]).toEqual({ use_supervisor: true })
    // args[7] = membership
    expect(args[7]).toEqual({
      membership_seed_input: "manual",
      room_agent_ids: ["agent-001", "agent-002"],
    })

    // Verify Zustand pending data has handoffMode: "prefill"
    const pendingData = useRoomUiStore.getState().pendingRoomData["new-room-123"]
    expect(pendingData).toBeDefined()
    expect(pendingData.initialMessage).toBe("Find top AI YouTubers")
    expect(pendingData.handoffMode).toBe("prefill")

    // Verify navigation
    expect(mockPush).toHaveBeenCalledWith("/room/new-room-123")
  })

  it("returns false and shows error when agent resolution fails", async () => {
    const { banner } = await import("@/components/ui/banner")
    const badTemplate: UseCaseTemplate = {
      ...template,
      agents: [{ agentId: "nonexistent", agentName: "Nonexistent Agent" }],
    }

    const { result } = renderHook(() =>
      useChatRoomCreation({
        userId: "user-1",
        userName: "Test User",
        getToken: vi.fn(),
        onRequireAuth: mockOnRequireAuth,
      }),
    )

    let success: boolean | undefined
    await act(async () => {
      success = await result.current.createFromTemplate(badTemplate, catalog)
    })

    expect(success).toBe(false)
    expect(mockCreateNewRoom).not.toHaveBeenCalled()
    expect(banner.error).toHaveBeenCalled()
  })

  it("dispatches rooms:refresh event before navigation", async () => {
    const dispatchSpy = vi.spyOn(window, "dispatchEvent")

    const { result } = renderHook(() =>
      useChatRoomCreation({
        userId: "user-1",
        userName: "Test User",
        getToken: vi.fn(),
        onRequireAuth: mockOnRequireAuth,
      }),
    )

    await act(async () => {
      await result.current.createFromTemplate(template, catalog)
    })

    const refreshEvent = dispatchSpy.mock.calls.find(
      (call) => (call[0] as Event).type === "rooms:refresh",
    )
    expect(refreshEvent).toBeDefined()
    dispatchSpy.mockRestore()
  })
})
