import { describe, it, expect, vi, beforeEach } from "vitest"
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react"
import type { Agent } from "@/lib/types/agent"
import { useRoomUiStore } from "@/stores/room-ui-store"

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
      capabilities: {}, protocolVersion: "1.0.0",
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
  useRoomUiStore.setState({ pendingChatDraft: null })
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
  const mod = await import("@/app/(portal)/chat/page")
  ChatPage = mod.default
})

describe("Chat page — Use Case Cards integration", () => {
  it("renders the two remaining use case cards with the section label", async () => {
    render(<ChatPage />)
    await waitFor(() => {
      expect(screen.getByText("Travel Planner")).toBeDefined()
      expect(screen.getByText("Story & Image Creator")).toBeDefined()
      expect(screen.getByText("Featured Use Cases")).toBeDefined()
    })
    expect(screen.queryByText("Creator Discovery & Export")).toBeNull()
  })

  it("calls createFromTemplate with catalog on card click", async () => {
    render(<ChatPage />)
    await waitFor(() => {
      expect(screen.getByText("Travel Planner")).toBeDefined()
    })
    fireEvent.click(screen.getByText("Travel Planner").closest("button")!)
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
      expect(cards.length).toBeGreaterThanOrEqual(2)
    })
  })

  it("consumes an Agent handoff as a focused mention draft", async () => {
    useRoomUiStore.getState().setPendingChatDraft(
      "<@a1|YouTube Creator Finder Agent> ",
    )

    const { container } = render(<ChatPage />)

    await waitFor(() => {
      expect(container.querySelector('.room-mention')?.textContent).toBe(
        'YouTube Creator Finder Agent',
      )
    })
    expect(document.activeElement).toHaveAttribute('contenteditable', 'true')
    expect(useRoomUiStore.getState().pendingChatDraft).toBeNull()
  })

  it("shows To Be Continued when catalog load fails", async () => {
    gmState = { ...gmState, agentsError: "Network error", availableAgents: [] }
    render(<ChatPage />)
    await waitFor(() => {
      expect(screen.getByText("To Be Continued")).toBeDefined()
    })
    expect(screen.queryByText("Failed to load agents")).toBeNull()
    expect(screen.queryByText("Retry")).toBeNull()
    expect(mockLoadAvailableAgents).not.toHaveBeenCalled()
  })
})
