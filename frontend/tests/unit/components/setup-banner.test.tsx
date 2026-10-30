import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { cleanup, fireEvent, render, screen, waitFor } from '../../utils/test-utils'
import { server } from '../../setup/msw-server'
import { getApiUrl } from '@/lib/utils'
import { OverviewSetupBanner } from '@/components/setup/setup-banner'

vi.mock('next/navigation', () => ({ usePathname: () => '/' }))

const DISMISSED_KEY = 'hybro.setup-notice.dismissed'
let status: Record<string, unknown>

beforeEach(() => {
  localStorage.clear()
  status = { state: 'unconfigured', image_size: 'auto', last_error: null }
  server.use(http.get(getApiUrl('setup/status'), () => HttpResponse.json(status)))
})

afterEach(cleanup)

describe('Overview setup banner', () => {
  it('links to the model provider settings and can be dismissed', async () => {
    render(<OverviewSetupBanner />)
    expect(await screen.findByTestId('setup-banner')).toHaveTextContent('No model provider is set up.')
    expect(screen.getByRole('link', { name: 'Open model provider settings' })).toHaveAttribute('href', '/?settings=model-provider')
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }))
    expect(screen.queryByTestId('setup-banner')).toBeNull()
    expect(localStorage.getItem(DISMISSED_KEY)).toBe('unconfigured')
  })

  it('stays dismissed for the same error code in this browser', async () => {
    localStorage.setItem(DISMISSED_KEY, 'unconfigured')
    render(<OverviewSetupBanner />)
    await waitFor(() => expect(screen.queryByTestId('setup-banner')).toBeNull())
  })

  it('shows again when the status reports a new error code', async () => {
    localStorage.setItem(DISMISSED_KEY, 'unconfigured')
    status = { state: 'credential_invalid', image_size: 'auto', last_error: null }
    render(<OverviewSetupBanner />)
    expect(await screen.findByTestId('setup-banner')).toHaveTextContent('credential is missing')
  })

  it('clears the dismissal once the provider is healthy', async () => {
    localStorage.setItem(DISMISSED_KEY, 'unconfigured')
    status = { state: 'configured', provider: 'openai', auth: 'api_key', text_model: 'gpt-5-mini', image_size: 'auto', last_error: null }
    render(<OverviewSetupBanner />)
    await waitFor(() => expect(localStorage.getItem(DISMISSED_KEY)).toBeNull())
    expect(screen.queryByTestId('setup-banner')).toBeNull()
  })

  it('shows nothing when the status cannot be read', async () => {
    server.use(http.get(getApiUrl('setup/status'), () => HttpResponse.json({ detail: 'Only administrators can change the model provider.', code: 'forbidden' }, { status: 403 })))
    render(<OverviewSetupBanner />)
    await new Promise(resolve => setTimeout(resolve, 50))
    expect(screen.queryByTestId('setup-banner')).toBeNull()
  })
})
