import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { server } from '../../setup/msw-server'
import { getApiUrl } from '@/lib/utils'
import { AGENT_CATALOG_KEY } from '@/hooks/room/useAgentCatalog'
import { ModelProviderSection } from '@/components/settings/model-provider-section'

// Every setup route is mocked; nothing reaches a backend or a model provider.
const unconfigured = { state: 'unconfigured', image_size: '1024x1024', last_error: null }

beforeEach(() => {
  server.use(
    http.get(getApiUrl('setup/status'), () => HttpResponse.json(unconfigured)),
    http.get(getApiUrl('setup/models'), () => HttpResponse.json({ text: ['deepseek-v4-flash'], image: [] })),
    http.post(getApiUrl('setup/api-key'), () => HttpResponse.json({
      changed: true,
      status: { state: 'configured', provider: 'deepseek', auth: 'api_key', text_model: 'deepseek-v4-flash', image_model: null, image_size: '1024x1024', last_error: null },
    })),
  )
})

afterEach(cleanup)

describe('ModelProviderSection', () => {
  it('refetches the agent catalog after a save, since the save re-gates sample agents', async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
    const invalidate = vi.spyOn(queryClient, 'invalidateQueries')
    render(<QueryClientProvider client={queryClient}><ModelProviderSection /></QueryClientProvider>)

    fireEvent.click(await screen.findByRole('radio', { name: 'DeepSeek' }))
    await waitFor(() => expect(screen.getByLabelText('Text model')).toHaveValue('deepseek-v4-flash'))
    fireEvent.change(screen.getByLabelText('DeepSeek API key'), { target: { value: 'sk-fixture' } })
    fireEvent.click(screen.getByRole('button', { name: 'Verify and save' }))

    await waitFor(() => expect(invalidate).toHaveBeenCalledWith({ queryKey: AGENT_CATALOG_KEY }))
  })
})
