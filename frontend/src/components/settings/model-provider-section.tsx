"use client"

import { useEffect, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink } from "lucide-react"
import { toast } from "sonner"

import { Alert, AlertDescription } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { SegmentedControl } from "@/components/ui/segmented-control"
import { SettingsCard } from "@/components/settings/settings-card"
import { LoadingButton } from "@/components/settings/loading-button"
import { FormGroup } from "@/components/settings/form-group"
import { setupStatusQueryKey, useSetupStatus } from "@/hooks/useSetupStatus"
import { AGENT_CATALOG_KEY } from "@/hooks/room/useAgentCatalog"
import {
  IMAGE_SIZES,
  cancelOAuth,
  completeOAuth,
  getOAuthLogin,
  getSetupModels,
  saveApiKey,
  saveImageSize,
  saveOAuthModels,
  setupError,
  setupErrorMessage,
  startOAuth,
  type AuthMethod,
  type ImageSize,
  type OAuthLogin,
  type ProviderId,
  type SetupStatus,
} from "@/lib/api/setup"
import { setupNotice } from "@/lib/setup-notice"
import { cn } from "@/lib/utils"

const PROVIDERS: { value: ProviderId; label: string }[] = [
  { value: "openai", label: "OpenAI" },
  { value: "deepseek", label: "DeepSeek" },
  { value: "anthropic", label: "Anthropic" },
]
const AUTH_LABELS: Record<AuthMethod, string> = { api_key: "API key", oauth: "ChatGPT sign-in" }
const STATE_LABELS: Record<SetupStatus["state"], string> = {
  configured: "Set up",
  unconfigured: "Not set up",
  credential_invalid: "Credential missing or invalid",
}
const LOGIN_POLL_MS = 2000

const SELECT_CLASS = cn(
  "border-input dark:bg-input/30 flex h-9 w-full min-w-0 rounded-md border bg-transparent px-3 py-1 text-base shadow-xs outline-none md:text-sm",
  "focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] disabled:cursor-not-allowed disabled:opacity-50",
)

function providerLabel(id: ProviderId): string {
  return PROVIDERS.find(provider => provider.value === id)?.label ?? id
}

function StatusSummary({ status }: { status: SetupStatus }) {
  const notice = setupNotice(status)
  const selection = status.provider && status.auth
    ? [providerLabel(status.provider), AUTH_LABELS[status.auth], status.text_model, status.image_model].filter(Boolean).join(" · ")
    : null
  return (
    <div className="space-y-1 text-sm" data-testid="model-provider-status">
      <p><span className="font-medium">{STATE_LABELS[status.state]}</span>{selection ? <span className="text-muted-foreground"> — {selection}</span> : null}</p>
      {notice ? <p className="text-status-warning">{notice.message}</p> : null}
    </div>
  )
}

/** Picks the saved model when it is still offered for this choice, else the recommendation. */
function initialModel(choices: readonly string[], saved: string | null | undefined, sameChoice: boolean): string {
  if (sameChoice && saved && choices.includes(saved)) return saved
  return choices[0] ?? ""
}

export function ModelProviderSection() {
  const queryClient = useQueryClient()
  const status = useSetupStatus()
  const current = status.data

  // Drafts stay null until the user changes them, so they follow the saved status.
  const [providerDraft, setProviderDraft] = useState<ProviderId | null>(null)
  const [authDraft, setAuthDraft] = useState<AuthMethod | null>(null)
  const provider = providerDraft ?? current?.provider ?? "openai"
  const auth: AuthMethod = provider === "openai" ? authDraft ?? current?.auth ?? "api_key" : "api_key"
  const sameChoice = current?.provider === provider && current?.auth === auth

  const models = useQuery({
    queryKey: ["setup", "models", provider, auth],
    queryFn: ({ signal }) => getSetupModels(provider, auth, signal),
    enabled: status.isSuccess,
    staleTime: Infinity,
    retry: false,
  })
  const [textDraft, setTextDraft] = useState<{ key: string; value: string } | null>(null)
  const [imageDraft, setImageDraft] = useState<{ key: string; value: string } | null>(null)
  const choiceKey = `${provider}/${auth}`
  const textModel = textDraft?.key === choiceKey ? textDraft.value : initialModel(models.data?.text ?? [], current?.text_model, sameChoice)
  const imageModel = imageDraft?.key === choiceKey
    ? imageDraft.value
    : sameChoice ? (current?.image_model ?? "") : (models.data?.image[0] ?? "")

  const [apiKey, setApiKey] = useState("")
  const [login, setLogin] = useState<OAuthLogin | null>(null)
  const [pasted, setPasted] = useState("")
  const [imageSizeDraft, setImageSizeDraft] = useState<ImageSize | null>(null)
  const imageSize = imageSizeDraft ?? current?.image_size ?? "1024x1024"
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const loginId = login?.state === "pending" ? login.login_id : null
  useEffect(() => {
    if (!loginId) return
    const timer = setInterval(() => {
      getOAuthLogin(loginId).then(setLogin).catch((failure: unknown) => {
        if (setupError(failure)?.code === "login_not_found") setLogin(null)
      })
    }, LOGIN_POLL_MS)
    return () => clearInterval(timer)
  }, [loginId])

  async function run(action: string, work: () => Promise<void>) {
    setBusy(action)
    setError(null)
    try {
      await work()
    } catch (failure) {
      setError(setupErrorMessage(failure))
    } finally {
      setBusy(null)
    }
  }

  function saved(status: SetupStatus) {
    queryClient.setQueryData(setupStatusQueryKey, status)
    // The save re-gated the sample agents, so their status changed.
    void queryClient.invalidateQueries({ queryKey: AGENT_CATALOG_KEY })
    setProviderDraft(null)
    setAuthDraft(null)
    setTextDraft(null)
    setImageDraft(null)
    toast.success("Model provider saved")
  }

  const selectedImage = imageModel === "" ? null : imageModel

  const submitApiKey = () => run("api-key", async () => {
    const result = await saveApiKey({ provider, api_key: apiKey, text_model: textModel, image_model: selectedImage })
    setApiKey("")
    saved(result.status)
  })

  const beginLogin = () => run("oauth-start", async () => {
    setPasted("")
    try {
      setLogin(await startOAuth())
    } catch (failure) {
      // One login at a time: resume the pending one instead of failing.
      const pending = setupError(failure)
      if (pending?.code !== "login_pending" || !pending.login_id) throw failure
      setLogin(await getOAuthLogin(pending.login_id))
    }
  })

  const submitPasted = (id: string) => run("oauth-complete", async () => {
    setLogin(await completeOAuth(id, pasted.trim()))
    setPasted("")
  })

  const cancelLogin = (id: string) => run("oauth-cancel", async () => {
    await cancelOAuth(id)
    setLogin(null)
  })

  const submitOAuthModels = (id: string | null) => run("oauth-models", async () => {
    const result = await saveOAuthModels({ login_id: id, text_model: textModel, image_model: selectedImage })
    setLogin(null)
    saved(result.status)
  })

  const submitImageSize = () => run("image-size", async () => {
    const result = await saveImageSize(imageSize)
    queryClient.setQueryData<SetupStatus>(setupStatusQueryKey, previous => previous && { ...previous, image_size: result.image_size })
    setImageSizeDraft(null)
    toast.success("Image size saved. Restart Hybro to apply it.")
  })

  if (status.isPending) {
    return (
      <SettingsCard title="Model provider" description="The model Hybro and its agents use">
        <p className="text-sm text-muted-foreground">Loading model provider…</p>
      </SettingsCard>
    )
  }
  if (status.isError) {
    return (
      <SettingsCard title="Model provider" description="The model Hybro and its agents use">
        <Alert variant="destructive"><AlertDescription>{setupErrorMessage(status.error)}</AlertDescription></Alert>
      </SettingsCard>
    )
  }

  const modelPickers = (
    <>
      <FormGroup id="setup-text-model" label="Text model" hint={models.isError ? undefined : "The first model is the recommended one."} error={models.isError ? setupErrorMessage(models.error) : undefined}>
        <select id="setup-text-model" className={SELECT_CLASS} value={textModel} disabled={!models.data} onChange={event => setTextDraft({ key: choiceKey, value: event.target.value })}>
          {(models.data?.text ?? []).map(model => <option key={model} value={model}>{model}</option>)}
        </select>
      </FormGroup>
      {models.data && models.data.image.length > 0 ? (
        <FormGroup id="setup-image-model" label="Image model" hint="Needed by agents that generate images.">
          <select id="setup-image-model" className={SELECT_CLASS} value={imageModel} onChange={event => setImageDraft({ key: choiceKey, value: event.target.value })}>
            <option value="">None</option>
            {models.data.image.map(model => <option key={model} value={model}>{model}</option>)}
          </select>
        </FormGroup>
      ) : null}
    </>
  )

  const savedOAuth = current?.state === "configured" && current.provider === "openai" && current.auth === "oauth"

  return (
    <SettingsCard title="Model provider" description="The model Hybro and its agents use" spacing={4}>
      {current ? <StatusSummary status={current} /> : null}

      <div className="flex flex-wrap gap-3">
        <SegmentedControl label="Provider" value={provider} options={PROVIDERS} onValueChange={value => { setProviderDraft(value); setError(null) }} />
        {provider === "openai" ? (
          <SegmentedControl
            label="Sign-in method"
            value={auth}
            options={[{ value: "api_key", label: AUTH_LABELS.api_key }, { value: "oauth", label: AUTH_LABELS.oauth }]}
            onValueChange={value => { setAuthDraft(value); setError(null) }}
          />
        ) : null}
      </div>

      {auth === "api_key" ? (
        <form className="space-y-4" onSubmit={event => { event.preventDefault(); void submitApiKey() }}>
          <FormGroup id="setup-api-key" label={`${providerLabel(provider)} API key`} hint="Sent once to verify it, then stored in auth.json on this machine.">
            <Input id="setup-api-key" type="password" autoComplete="off" value={apiKey} onChange={event => setApiKey(event.target.value)} />
          </FormGroup>
          {modelPickers}
          <LoadingButton type="submit" loading={busy === "api-key"} disabled={busy !== null || !apiKey.trim() || !textModel}>
            Verify and save
          </LoadingButton>
        </form>
      ) : login === null ? (
        <div className="space-y-4">
          {savedOAuth ? modelPickers : null}
          <div className="flex flex-wrap gap-2">
            {savedOAuth ? (
              <LoadingButton loading={busy === "oauth-models"} disabled={busy !== null || !textModel} onClick={() => { void submitOAuthModels(null) }}>
                Save models
              </LoadingButton>
            ) : null}
            <LoadingButton variant={savedOAuth ? "secondary" : "default"} loading={busy === "oauth-start"} disabled={busy !== null} onClick={() => { void beginLogin() }}>
              {savedOAuth ? "Sign in again" : "Sign in with ChatGPT"}
            </LoadingButton>
          </div>
        </div>
      ) : login.state === "pending" ? (
        <div className="space-y-4" data-testid="oauth-pending">
          <ol className="list-decimal space-y-1 pl-5 text-sm">
            <li>Open the sign-in page and approve Hybro.</li>
            <li>This page updates by itself when the sign-in finishes.</li>
            <li>If the browser then shows an error page at localhost:1455, copy that page's full address and paste it below.</li>
          </ol>
          {login.authorization_url ? (
            <Button asChild>
              <a href={login.authorization_url} target="_blank" rel="noopener noreferrer">
                Open the sign-in page<ExternalLink data-icon="inline-end" aria-hidden="true" />
              </a>
            </Button>
          ) : null}
          <form className="space-y-2" onSubmit={event => { event.preventDefault(); void submitPasted(login.login_id) }}>
            <FormGroup id="setup-redirect-url" label="Address from the localhost:1455 page">
              <Input id="setup-redirect-url" autoComplete="off" placeholder="http://localhost:1455/auth/callback?code=…" value={pasted} onChange={event => setPasted(event.target.value)} />
            </FormGroup>
            <div className="flex flex-wrap gap-2">
              <LoadingButton type="submit" variant="secondary" loading={busy === "oauth-complete"} disabled={busy !== null || !pasted.trim()}>
                Finish sign-in
              </LoadingButton>
              <LoadingButton type="button" variant="ghost" loading={busy === "oauth-cancel"} disabled={busy !== null} onClick={() => { void cancelLogin(login.login_id) }}>
                Cancel
              </LoadingButton>
            </div>
          </form>
        </div>
      ) : login.state === "authenticated" ? (
        <div className="space-y-4" data-testid="oauth-authenticated">
          <p className="text-sm">Signed in. Choose the models to use.</p>
          {modelPickers}
          <LoadingButton loading={busy === "oauth-models"} disabled={busy !== null || !textModel} onClick={() => { void submitOAuthModels(login.login_id) }}>
            Verify and save
          </LoadingButton>
        </div>
      ) : (
        <div className="space-y-4">
          <Alert variant="destructive">
            <AlertDescription>{login.error ?? (login.state === "expired" ? "The sign-in expired." : "The sign-in failed.")}</AlertDescription>
          </Alert>
          <LoadingButton loading={busy === "oauth-start"} disabled={busy !== null} onClick={() => { void beginLogin() }}>
            Start again
          </LoadingButton>
        </div>
      )}

      {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null}

      <form className="space-y-2 border-t pt-4" onSubmit={event => { event.preventDefault(); void submitImageSize() }}>
        <FormGroup id="setup-image-size" label="Image size" hint="Takes effect after Hybro restarts.">
          <select id="setup-image-size" className={SELECT_CLASS} value={imageSize} onChange={event => setImageSizeDraft(event.target.value as ImageSize)}>
            {IMAGE_SIZES.map(size => <option key={size} value={size}>{size}</option>)}
          </select>
        </FormGroup>
        <LoadingButton type="submit" variant="secondary" loading={busy === "image-size"} disabled={busy !== null || imageSize === current?.image_size}>
          Save image size
        </LoadingButton>
      </form>
    </SettingsCard>
  )
}
