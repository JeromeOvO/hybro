import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { SidebarProvider } from '@/components/ui/sidebar'
import { DocsButton } from '@/components/nav-docs-button'

vi.mock('@/hooks/use-mobile', () => ({ useIsMobile: () => false }))

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

describe('DocsButton', () => {
  it('opens the docs on the Hybro website in a new tab', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    render(<SidebarProvider><DocsButton /></SidebarProvider>)
    fireEvent.click(screen.getByRole('button', { name: /Documentation/ }))
    expect(open).toHaveBeenCalledWith('https://hybro.ai/docs', '_blank')
  })
})
