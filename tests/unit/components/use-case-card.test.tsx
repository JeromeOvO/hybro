import { describe, it, expect, vi } from "vitest"
import { render, fireEvent, within } from "@testing-library/react"
import { UseCaseCard } from "@/components/use-case-card"
import type { UseCaseTemplate } from "@/lib/use-case-templates"
import { Youtube } from "lucide-react"

const template: UseCaseTemplate = {
  id: "test-card",
  icon: Youtube,
  iconGradient: ["#ff0050", "#ff4080"],
  title: "YouTube Creator Finder",
  description: "Find YouTuber contact info by topic",
  agents: [
    { agentId: "a1", agentName: "Agent One", iconUrl: "https://example.com/a1.png" },
    { agentId: "a2", agentName: "Agent Two" },
  ],
  prefillMessage: "Find YouTubers",
  tag: "new",
}

describe("UseCaseCard", () => {
  it("renders title and description", () => {
    const { container } = render(<UseCaseCard template={template} onClick={vi.fn()} />)
    expect(within(container).getByText("YouTube Creator Finder")).toBeDefined()
    expect(within(container).getByText("Find YouTuber contact info by topic")).toBeDefined()
  })

  it("renders agent count", () => {
    const { container } = render(<UseCaseCard template={template} onClick={vi.fn()} />)
    expect(within(container).getByText("2 agents")).toBeDefined()
  })

  it('renders "New" tag when tag is "new"', () => {
    const { container } = render(<UseCaseCard template={template} onClick={vi.fn()} />)
    expect(within(container).getByText("New")).toBeDefined()
  })

  it('does not render tag when tag is null', () => {
    const noTagTemplate: UseCaseTemplate = { ...template, tag: null }
    const { container } = render(<UseCaseCard template={noTagTemplate} onClick={vi.fn()} />)
    expect(within(container).queryByText("New")).toBeNull()
  })

  it("calls onClick when clicked", () => {
    const onClick = vi.fn()
    const { container } = render(<UseCaseCard template={template} onClick={onClick} />)
    fireEvent.click(within(container).getByRole("button"))
    expect(onClick).toHaveBeenCalledOnce()
  })

  it("does not call onClick when disabled", () => {
    const onClick = vi.fn()
    const { container } = render(<UseCaseCard template={template} onClick={onClick} disabled />)
    fireEvent.click(within(container).getByRole("button"))
    expect(onClick).not.toHaveBeenCalled()
  })

  it("renders agent avatars with fallback letters", () => {
    const { container } = render(<UseCaseCard template={template} onClick={vi.fn()} />)
    const fallbacks = within(container).getAllByTestId("avatar-fallback")
    expect(fallbacks.length).toBe(1) // Only Agent Two has no iconUrl
    expect(fallbacks[0].textContent).toBe("A")
  })
})
