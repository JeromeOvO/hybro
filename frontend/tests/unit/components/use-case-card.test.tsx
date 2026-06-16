import { describe, it, expect, vi } from "vitest"
import { render, fireEvent, within } from "@testing-library/react"
import { UseCaseCard } from "@/components/use-case-card"
import type { UseCaseTemplate } from "@/lib/use-case-templates"
import type { Agent } from "@/lib/types/agent"
import { Youtube } from "lucide-react"

function makeAgent(id: string, name: string, iconUrl?: string): Agent {
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
      ...(iconUrl ? { iconUrl } : {}),
    },
  }
}

const catalog: Agent[] = [
  makeAgent("a1", "Agent One", "https://example.com/a1.png"),
  makeAgent("a2", "Agent Two"),
]

const template: UseCaseTemplate = {
  id: "test-card",
  icon: Youtube,
  title: "YouTube Creator Finder",
  description: "Find YouTuber contact info by topic",
  agents: [
    { agentId: "a1", agentName: "Agent One" },
    { agentId: "a2", agentName: "Agent Two" },
  ],
  prefillMessage: "Find YouTubers",
  tag: "new",
}

describe("UseCaseCard", () => {
  it("renders title and description", () => {
    const { container } = render(<UseCaseCard template={template} catalog={catalog} onClick={vi.fn()} />)
    expect(within(container).getByText("YouTube Creator Finder")).toBeDefined()
    expect(within(container).getByText("Find YouTuber contact info by topic")).toBeDefined()
  })

  it("renders agent count", () => {
    const { container } = render(<UseCaseCard template={template} catalog={catalog} onClick={vi.fn()} />)
    expect(within(container).getByText("2 agents")).toBeDefined()
  })

  it('renders "New" tag when tag is "new"', () => {
    const { container } = render(<UseCaseCard template={template} catalog={catalog} onClick={vi.fn()} />)
    expect(within(container).getByText("New")).toBeDefined()
  })

  it('does not render tag when tag is null', () => {
    const noTagTemplate: UseCaseTemplate = { ...template, tag: null }
    const { container } = render(<UseCaseCard template={noTagTemplate} catalog={catalog} onClick={vi.fn()} />)
    expect(within(container).queryByText("New")).toBeNull()
  })

  it("calls onClick when clicked", () => {
    const onClick = vi.fn()
    const { container } = render(<UseCaseCard template={template} catalog={catalog} onClick={onClick} />)
    fireEvent.click(within(container).getByRole("button"))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("does not call onClick when disabled", () => {
    const onClick = vi.fn()
    const { container } = render(<UseCaseCard template={template} catalog={catalog} onClick={onClick} disabled />)
    fireEvent.click(within(container).getByRole("button"))
    expect(onClick).not.toHaveBeenCalled()
  })

  it("resolves avatar from catalog iconUrl", () => {
    const { container } = render(<UseCaseCard template={template} catalog={catalog} onClick={vi.fn()} />)
    const imgs = container.querySelectorAll("img")
    // Agent One has iconUrl from catalog, Agent Two uses dicebear fallback
    expect(imgs.length).toBe(2)
    expect(imgs[0].getAttribute("src")).toBe("https://example.com/a1.png")
    // Agent Two should get a dicebear data URI fallback
    expect(imgs[1].getAttribute("src")).toMatch(/^data:image\/svg\+xml/)
  })
})
