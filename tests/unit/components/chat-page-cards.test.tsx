import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react"
import type { Agent } from "@/lib/types/agent"

// --- Mocks ---

const mockCreateFromTemplate = vi.fn().mockResolvedValue(true)
const mockCreateAndNavigate = vi.fn().mockResolvedValue(true)
const mockLoadAvailableAgents = vi.fn()

function makeAgent(id: string, name: string): Agent {
  return {
    agent_id: id,
    agent_card: {
      name, description: "", url: `https://ex.com/${id}`,
      version: "1.0.0", provider: { organization: "test", url: "https://test.com" },
      skills: [], defaultInputModes: ["text"], defaultOutputModes: ["text"],
    },
  }
}
const agents = [makeAgent("a1", "YouTube Creator Finder Agent"), makeAgent("a2", "GPT-5-mini Agent")]

// Mock useGroupManagement — returns controllable state
let gmState: Record<string, unknown> = {}
vi.mock("@/hooks/useGroupManagement", () => ({
  useGroupManagement: () => gmState,
}))

// Mock useChatRoomCreation
vi.mock("@/hooks/useChatRoomCreation", () => ({
  useChatRoomCreation: () => ({
    creating: false,
    createAndNavigate: mockCreateAndNavigate,
    createFromTemplate: mockCreateFromTemplate,
    loadDefaultAgents: vi.fn(),
    getAgentSuggestions: vi.fn(),
    createRoomWithMessage: vi.fn(),
    createWithAgentsAndNavigate: vi.fn(),
    defaultAgents: [],
  }),
}))

// Mock Clerk
vi.mock("@clerk/nextjs", () => ({
  useUser: () => ({ user: { id: "user-1", firstName: "Test" }, isLoaded: true }),
  useAuth: () => ({ getToken: vi.fn() }),
}))

// Mock next/navigation
vi.mock("next/navigation", () => ({
  useSearchParams: () => new URLSearchParams(),
  useRouter: () => ({ push: vi.fn() }),
}))

// Mock GroupManagementModal to avoid rendering complexity
vi.mock("@/components/group-management-modal", () => ({
  GroupManagementModal: () => null,
}))

// Dynamic import of the page component (after mocks are set up)
let ChatPage: React.ComponentType

beforeEach(async () => {
  cleanup()
  vi.clearAllMocks()
  gmState = {
    availableAgents: agents,
    loadingAgents: false,
    agentsError: null,
    groups: [],
    loadingGroups: false,
    selectedGroup: "all_agents",
    isOverride: false,
    resolvedTargetMode: { message_target_mode: "all_agents" },
    groupManagementOpen: false,
    groupAction: null,
    handleGroupsChange: vi.fn(),
    handleCreateGroup: vi.fn(),
    handleEditGroup: vi.fn(),
    handleDeleteGroup: vi.fn(),
    handleGroupCreated: vi.fn(),
    handleGroupChange: vi.fn(),
    handleClearOverride: vi.fn(),
    setGroupManagementOpen: vi.fn(),
    setGroupAction: vi.fn(),
    loadAvailableAgents: mockLoadAvailableAgents,
    setAvailableAgents: vi.fn(),
  }
  const mod = await import("@/app/c/chat/page")
  ChatPage = mod.default
})

describe("Chat page — Use Case Cards integration", () => {
  it("renders use case card titles", async () => {
    render(<ChatPage />)
    await waitFor(() => {
      expect(screen.getByText("YouTube Creator Finder")).toBeDefined()
      expect(screen.getByText("Travel Planner")).toBeDefined()
      expect(screen.getByText("Image Generator")).toBeDefined()
    })
  })

  it("calls createFromTemplate with catalog on card click", async () => {
    render(<ChatPage />)
    await waitFor(() => {
      expect(screen.getByText("YouTube Creator Finder")).toBeDefined()
    })
    fireEvent.click(screen.getByText("YouTube Creator Finder").closest("button")!)
    await waitFor(() => {
      expect(mockCreateFromTemplate).toHaveBeenCalledOnce()
      expect(mockCreateFromTemplate.mock.calls[0][1]).toEqual(agents)
    })
  })

  it("disables cards when catalog is loading", async () => {
    gmState = { ...gmState, loadingAgents: true, availableAgents: [] }
    const { container } = render(<ChatPage />)
    await waitFor(() => {
      const cards = container.querySelectorAll("button[disabled]")
      expect(cards.length).toBeGreaterThanOrEqual(3)
    })
  })

  it("shows error state with retry when catalog load fails", async () => {
    gmState = { ...gmState, agentsError: "Network error", availableAgents: [] }
    render(<ChatPage />)
    await waitFor(() => {
      expect(screen.getByText("Failed to load agents")).toBeDefined()
      expect(screen.getByText("Retry")).toBeDefined()
    })
    fireEvent.click(screen.getByText("Retry"))
    expect(mockLoadAvailableAgents).toHaveBeenCalledOnce()
  })
})
