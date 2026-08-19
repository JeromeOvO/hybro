import { describe, it, expect, vi, beforeEach } from "vitest"
import { renderHook, act } from "@testing-library/react"
import { useChatRoomCreation } from "@/hooks/useChatRoomCreation"
import { useRoomUiStore } from "@/stores/room-ui-store"
import type { UseCaseTemplate } from "@/lib/use-case-templates"
import type { Agent } from "@/lib/types/agent"
import { Palmtree } from "lucide-react"

const mockPush = vi.fn()
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mockPush }),
}))

const mockCreateNewRoom = vi.fn()
vi.mock("@/lib/api/room", () => ({
  createNewRoom: (...args: unknown[]) => mockCreateNewRoom(...args),
}))

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
  makeAgent("weather-1", "Weather Agent"),
  makeAgent("travel-1", "Travel Planner Agent"),
]

const template: UseCaseTemplate = {
  id: "travel-planner",
  icon: Palmtree,
  title: "Travel Planner",
  description: "Plan a trip",
  agents: [
    { agentId: "weather-1", agentName: "Weather Agent" },
    { agentId: "travel-1", agentName: "Travel Planner Agent" },
  ],
  prefillMessage: "Generate a travel plan for Hawaii",
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

  it("creates a room named after the template and stores a prefill handoff", async () => {
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
    expect(mockCreateNewRoom).toHaveBeenCalledOnce()
    const args = mockCreateNewRoom.mock.calls[0]
    expect(args[0]).toBe("Travel Planner")
    expect(args[1]).toBe("user-1")
    expect(args[2]).toBe("Test User")
    expect(args[4]).toEqual({
      "weather-1": "Weather Agent",
      "travel-1": "Travel Planner Agent",
    })
    expect(args[5]).toEqual({ use_supervisor: true })
    expect(args[7]).toEqual({
      membership_seed_input: "manual",
      room_agent_ids: ["weather-1", "travel-1"],
    })

    const pendingData = useRoomUiStore.getState().pendingRoomData["new-room-123"]
    expect(pendingData).toBeDefined()
    expect(pendingData.initialMessage).toBe("Generate a travel plan for Hawaii")
    expect(pendingData.handoffMode).toBe("prefill")
    expect(mockPush).toHaveBeenCalledWith("/room/new-room-123")
  })

  it("returns false and shows an error when agent resolution fails", async () => {
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
})
