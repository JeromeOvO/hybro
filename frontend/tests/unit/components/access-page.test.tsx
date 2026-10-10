import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { AccessPage } from '@/components/access/access-page'
import { MCP_CONFIG, MCP_URL } from '@/lib/mcp'

vi.mock('@/lib/config', () => ({ publicConfig: () => ({ api_base_url: 'https://hybro.example', api_prefix: '/api/v1' }) }))

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ service: 'hybro-mcp', status: 'ready' }) }))
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: vi.fn().mockResolvedValue(undefined) } })
})

afterEach(() => {
  cleanup()
  vi.unstubAllGlobals()
})

function renderPage(kind: 'mcp' | 'api' = 'mcp') {
  return render(<AccessPage kind={kind} />)
}

describe('Access pages', () => {
  it('preserves complete HTTP configuration and URL copying without executing agent calls', async () => {
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Copy URL' }))
    await screen.findByText('URL copied')
    expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith(MCP_URL)
    fireEvent.click(screen.getByRole('button', { name: 'Copy configuration' }))
    await screen.findByText('Configuration copied')
    expect(navigator.clipboard.writeText).toHaveBeenLastCalledWith(MCP_CONFIG)
    expect(vi.mocked(fetch).mock.calls.every(([url]) => url === '/hybro-mcp')).toBe(true)
  })

  it('switches between configuration and all-agent prompts with the keyboard', async () => {
    const user = userEvent.setup()
    renderPage()
    await user.click(screen.getByRole('tab', { name: 'Connection configuration' }))
    await user.keyboard('{ArrowRight}')
    await waitFor(() => expect(screen.getByRole('tab', { name: 'AI setup prompt' })).toHaveFocus())
    expect(screen.getByLabelText('Access material')).toHaveTextContent('discover_agents({})')
    await user.click(screen.getByRole('button', { name: 'Copy prompt' }))
    const prompt = await navigator.clipboard.readText()
    expect(prompt).toContain(MCP_URL)
    expect(prompt).toContain('discover_agents({})')
    await user.click(screen.getByRole('tab', { name: 'AI setup prompt' }))
    await user.keyboard('{Home}')
    await waitFor(() => expect(screen.getByRole('tab', { name: 'Connection configuration' })).toHaveFocus())
    expect(screen.getByLabelText('Access material').textContent).toBe(MCP_CONFIG)
  })

  it('copies an all-agent API request without requiring a token or fetching data', async () => {
    const user = userEvent.setup()
    renderPage('api')
    await user.click(screen.getByRole('button', { name: 'Copy request' }))
    const request = await navigator.clipboard.readText()
    expect(request).toContain('https://hybro.example/api/v1/agents/discovery')
    expect(request).not.toMatch(/authorization|bearer|access_token|group_id/i)
    expect(screen.getByLabelText('Access material')).not.toHaveTextContent(/authorization|bearer|access_token|group_id/i)
    expect(fetch).not.toHaveBeenCalled()
  })

  it('offers manual selection without claiming clipboard success', async () => {
    vi.mocked(navigator.clipboard.writeText).mockRejectedValue(new Error('Denied'))
    renderPage()
    fireEvent.click(screen.getByRole('button', { name: 'Copy configuration' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Select and copy the text manually')
    expect(screen.getByLabelText('Access material')).toHaveFocus()
    expect(screen.getByLabelText('Access material').textContent).toBe(MCP_CONFIG)
    expect(screen.queryByText('Configuration copied')).not.toBeInTheDocument()
  })

  it.each([
    ['unavailable', 'Unavailable', 'Start services or check MCP logs in the Hybro TUI.'],
    ['unsupported_auth', 'Unavailable with Clerk', 'This local adapter does not support Clerk authentication.']
  ])('preserves honest local adapter status for %s', async (status, label, message) => {
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ service: 'hybro-mcp', status }) } as Response)
    renderPage()
    await screen.findByText(label, { exact: true })
    expect(screen.getByText(message)).toBeInTheDocument()
    vi.mocked(fetch).mockResolvedValue({ ok: true, json: async () => ({ service: 'hybro-mcp', status: 'ready' }) } as Response)
    fireEvent.click(screen.getByRole('button', { name: 'Refresh MCP status' }))
    await screen.findByText('Ready', { exact: true })
  })

  it('cancels a pending MCP status request when leaving the page', async () => {
    vi.mocked(fetch).mockImplementation(() => new Promise(() => {}))
    const view = renderPage()
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce())
    const signal = vi.mocked(fetch).mock.calls[0][1]?.signal
    view.unmount()
    expect(signal?.aborted).toBe(true)
  })
})
