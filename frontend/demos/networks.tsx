'use client'

import { useCallback, useEffect, useRef, useState, type CSSProperties, type FormEvent } from 'react'
import { createRoot } from 'react-dom/client'
import {
  ArrowRight, Copy, Globe, Maximize, MessageCirclePlus,
  Minus, Network as NetworkIcon, Pencil, Plus, RotateCcw, Trash2, X,
} from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from '@/components/ui/dialog'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Separator } from '@/components/ui/separator'
import { Toaster } from '@/components/ui/sonner'
import { routes } from '@/lib/routes'
import { NetworkCanvas } from './network-canvas'
import {
  agents, initialNetworks, networkColors,
  type CanvasHandle, type CanvasSelection, type DemoAgent, type DemoNetwork,
} from './network-model'

// The standalone demo is served on :8010; Agents remains in the existing app.
const agentsPageUrl = new URL(routes.agents, 'http://localhost:3000').href

type Confirmation =
  | { type: 'delete'; network: DemoNetwork }
  | { type: 'remove'; network: DemoNetwork; agentId: string }

function AgentChoices({
  candidates, chosen, existingIds, onToggle,
}: {
  candidates: DemoAgent[]
  chosen: ReadonlySet<string>
  existingIds?: readonly string[]
  onToggle: (id: string, checked: boolean) => void
}) {
  return (
    <>
      {candidates.map(candidate => {
        const added = existingIds?.includes(candidate.id) ?? false
        return (
          <label key={candidate.id} className="flex items-center gap-3 rounded-md border p-3">
            <input
              type="checkbox"
              className="size-4 shrink-0 accent-primary"
              disabled={added}
              checked={added || chosen.has(candidate.id)}
              aria-label={`${added ? '已加入' : '选择'} ${candidate.name}`}
              onChange={event => onToggle(candidate.id, event.target.checked)}
            />
            <span className="min-w-0 flex-1">
              <span className="block text-sm font-medium">{candidate.name}</span>
              <span className="mt-1 block text-xs text-muted-foreground">{candidate.description}</span>
            </span>
            {added ? <Badge variant="secondary">已加入</Badge> : candidate.status === 'inactive' ? <Badge variant="inactive">Inactive</Badge> : null}
          </label>
        )
      })}
      {candidates.length === 0 ? <p className="py-5 text-center text-sm text-muted-foreground">没有匹配的 Agent。</p> : null}
    </>
  )
}

function NetworksDemo() {
  const [networks, setNetworks] = useState(() => structuredClone(initialNetworks))
  const [selection, setSelection] = useState<CanvasSelection | null>(null)
  const [zoom, setZoom] = useState(100)
  const [editor, setEditor] = useState<{ id: string | null } | null>(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [nameError, setNameError] = useState(false)
  const [picker, setPicker] = useState(false)
  const [agentQuery, setAgentQuery] = useState('')
  const [chosen, setChosen] = useState<Set<string>>(() => new Set())
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null)
  const canvas = useRef<CanvasHandle>(null)
  const pendingFocus = useRef<string | null>(null)

  const current = networks.find(network => network.id === selection?.networkId)
  const selectedAgent = agents.find(agent => agent.id === selection?.agentId)
  const query = agentQuery.trim().toLowerCase()
  const candidates = agents.filter(agent =>
    `${agent.name} ${agent.description} ${agent.skill}`.toLowerCase().includes(query),
  )

  useEffect(() => {
    if (!pendingFocus.current) return
    canvas.current?.focusNetwork(pendingFocus.current)
    pendingFocus.current = null
  }, [networks, selection])

  const selectNetwork = useCallback((networkId: string) => {
    pendingFocus.current = networkId
    setSelection({ networkId, agentId: null })
  }, [])

  const openMembers = useCallback((networkId: string) => {
    setSelection({ networkId, agentId: null })
    setChosen(new Set())
    setAgentQuery('')
    setPicker(true)
  }, [])

  function openEditor(network?: DemoNetwork) {
    setName(network?.name ?? '')
    setDescription(network?.description ?? '')
    setNameError(false)
    setChosen(new Set())
    setAgentQuery('')
    setEditor({ id: network?.id ?? null })
  }

  function toggleChosen(id: string, checked: boolean) {
    setChosen(previous => {
      const next = new Set(previous)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  function saveNetwork(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) {
      setNameError(true)
      return
    }
    if (editor?.id) {
      const id = editor.id
      setNetworks(previous => previous.map(network => network.id === id
        ? { ...network, name: trimmed, description: description.trim() }
        : network))
      toast.success('Network 已更新')
    } else {
      const id = `demo-${crypto.randomUUID()}`
      const next: DemoNetwork = {
        id, name: trimmed, description: description.trim(), members: [...chosen],
        color: networkColors[networks.length % networkColors.length],
        x: Math.max(325, ...networks.map(network => network.x)) + 630,
        y: 280,
      }
      pendingFocus.current = id
      setNetworks(previous => [...previous, next])
      setSelection({ networkId: id, agentId: null })
      toast.success(chosen.size
        ? `Network 已创建，包含 ${chosen.size} 个 Agent`
        : 'Network 已创建，可以继续添加 Agent')
    }
    setEditor(null)
  }

  function addMembers() {
    if (!current || chosen.size === 0) return
    const id = current.id
    setNetworks(previous => previous.map(network => network.id === id
      ? { ...network, members: [...new Set([...network.members, ...chosen])] }
      : network))
    setSelection({ networkId: id, agentId: null })
    pendingFocus.current = id
    setPicker(false)
    toast.success(`已添加 ${chosen.size} 个 Agent，画布已更新`)
  }

  function confirmChange() {
    if (!confirmation) return
    const { network } = confirmation
    if (confirmation.type === 'delete') {
      setNetworks(previous => previous.filter(item => item.id !== network.id))
      setSelection(null)
      toast.success('Network 已删除，Agent 保留')
    } else {
      const { agentId } = confirmation
      setNetworks(previous => previous.map(item => item.id === network.id
        ? { ...item, members: item.members.filter(id => id !== agentId) }
        : item))
      setSelection({ networkId: network.id, agentId: null })
      toast.success('成员已移除，其他 Network 不受影响')
    }
    setConfirmation(null)
  }

  function overview() {
    setSelection(null)
    canvas.current?.fitAll()
  }

  async function copy(text: string) {
    try {
      await navigator.clipboard.writeText(text)
      toast.success('已复制；Network ID 为演示数据')
    } catch {
      toast.error('浏览器不允许复制，请手动选择文本复制')
    }
  }

  return (
    <>
      <div className="app">
        <aside className="sidebar" aria-label="主导航">
          <div className="flex items-center gap-2 px-3 text-xl font-bold">
            <NetworkIcon className="size-7 text-primary" aria-hidden="true" />
            Hybro
          </div>
          <p className="my-6 flex items-center gap-2 px-3 text-xs text-muted-foreground">
            <span className="size-1.5 rounded-full bg-primary" />本地工作区
          </p>
          <nav className="flex flex-col gap-1">
            <Button variant="ghost" className="justify-start" disabled title="演示不包含聊天页面">
              <MessageCirclePlus data-icon="inline-start" />New chat
            </Button>
            <Button variant="ghost" className="justify-start" asChild>
              <a href={agentsPageUrl}>
                <Globe data-icon="inline-start" />Agents
              </a>
            </Button>
            <Button variant="brandTint" className="justify-start" aria-current="page" onClick={overview}>
              <NetworkIcon data-icon="inline-start" />Networks
            </Button>
          </nav>
          <div className="mt-auto flex items-center gap-2 px-3 text-xs text-muted-foreground">
            <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-muted">L</span>
            <div>Local workspace<p className="mt-1 text-xs">仅限本机演示</p></div>
          </div>
        </aside>

        <main className="workspace-main" aria-label="Networks 页面">
          <h1 className="sr-only">Networks</h1>
          <div className="canvas-layout">
            <aside className="network-index" aria-label="我的 Networks">
              <div className="flex items-center justify-between gap-2">
                <h2 className="text-sm font-medium">我的 Networks</h2>
                <Badge variant="secondary">{networks.length}</Badge>
              </div>
              <Button className="my-4 w-full" onClick={() => openEditor()} data-action="create">
                <Plus data-icon="inline-start" />新建 Network
              </Button>
              <nav id="network-index" className="flex shrink-0 flex-col gap-1" aria-label="Network 列表">
                {networks.map(network => (
                  <Button
                    key={network.id}
                    variant={selection?.networkId === network.id ? 'secondary' : 'ghost'}
                    className="index-row w-full justify-start"
                    onClick={() => selectNetwork(network.id)}
                    data-network-id={network.id}
                    aria-current={selection?.networkId === network.id ? 'true' : undefined}
                  >
                    <span className="size-1.5 shrink-0 rounded-full" style={{ backgroundColor: network.color }} />
                    <span className="min-w-0 flex-1 truncate text-left">{network.name}</span>
                    <span className="text-xs text-muted-foreground">{network.members.length}</span>
                  </Button>
                ))}
                {networks.length === 0 ? <p className="py-3 text-xs text-muted-foreground">还没有 Network，点击上方按钮创建。</p> : null}
              </nav>
            </aside>

            <div className="stage" id="stage">
              <NetworkCanvas
                ref={canvas}
                networks={networks}
                selection={selection}
                showShared={false}
                onSelect={setSelection}
                onAddAgent={openMembers}
                onZoomChange={setZoom}
              />
              {networks.length === 0 ? (
                <div className="canvas-empty">
                  <NetworkIcon className="size-10 text-muted-foreground" aria-hidden="true" />
                  <h2 className="text-lg font-medium">画布还没有 Network</h2>
                  <p className="text-sm text-muted-foreground">从左侧新建 Network，再加入 Agent。</p>
                </div>
              ) : null}

              {current ? (
                <section className="inspector" aria-label="选中对象详情">
                  <div className="flex items-start justify-between gap-3 p-4">
                    <div className="min-w-0 flex-1">
                      <h2 className="wrap-anywhere text-base font-semibold">{selectedAgent?.name ?? current.name}</h2>
                      <p className="mt-2 wrap-anywhere text-xs leading-relaxed text-muted-foreground">
                        {selectedAgent?.description ?? (current.description || '还没有用途描述')}
                      </p>
                    </div>
                    <Button variant="ghost" size="icon" onClick={() => setSelection(null)} aria-label="关闭详情">
                      <X />
                    </Button>
                  </div>
                  <Separator />
                  {selectedAgent ? (
                    <>
                      <div className="flex flex-col gap-4 p-4">
                        <div className="flex items-center justify-between gap-2">
                          <span className="text-xs">注册状态</span>
                          <Badge variant={selectedAgent.status === 'active' ? 'success' : 'inactive'}>
                            {selectedAgent.status === 'active' ? 'Active' : 'Inactive'}
                          </Badge>
                        </div>
                        <p className="text-xs text-muted-foreground">注册状态不是实时连通性检测。</p>
                        <h3 className="text-xs font-medium">出现于这些 Networks</h3>
                        {networks.filter(network => network.members.includes(selectedAgent.id)).map(network => (
                          <Button key={network.id} variant="outline" className="w-full justify-between" onClick={() => selectNetwork(network.id)}>
                            <span className="truncate">{network.name}</span><ArrowRight data-icon="inline-end" />
                          </Button>
                        ))}
                        <p className="text-xs leading-relaxed text-muted-foreground">同名节点引用同一个 Agent，不是多个实例。</p>
                        <Button variant="outline" onClick={() => setConfirmation({ type: 'remove', network: current, agentId: selectedAgent.id })}>
                          <Minus data-icon="inline-start" />从当前 Network 移除
                        </Button>
                      </div>
                    </>
                  ) : (
                    <>
                      <div className="flex flex-col gap-2 p-4">
                        <h3 className="text-xs font-medium">Network ID</h3>
                        <div className="flex items-center gap-2">
                          <code className="min-w-0 flex-1 break-all text-xs text-muted-foreground">{current.id}</code>
                          <Button variant="ghost" size="icon" aria-label="复制 Network ID" onClick={() => copy(current.id)}><Copy /></Button>
                        </div>
                      </div>
                      <Separator />
                      <div className="flex flex-col gap-2 p-4">
                        <div className="flex items-center justify-between">
                          <h3 className="text-xs font-medium">Agent 成员 · {current.members.length}</h3>
                          <Button variant="ghost" size="icon" aria-label="添加 Agent" onClick={() => openMembers(current.id)}>
                            <Plus />
                          </Button>
                        </div>
                        {current.members.map(id => {
                          const member = agents.find(item => item.id === id)!
                          return (
                            <div key={id} className="flex items-center gap-1">
                              <Button variant="ghost" size="sm" className="min-w-0 flex-1 justify-start" onClick={() => setSelection({ networkId: current.id, agentId: id })}>
                                <span className="size-1.5 shrink-0 rounded-full" style={{ backgroundColor: current.color }} />
                                <span className="truncate">{member.name}</span>
                              </Button>
                              <Button variant="ghost" size="icon" aria-label={`移除 ${member.name}`} onClick={() => setConfirmation({ type: 'remove', network: current, agentId: id })}>
                                <Minus />
                              </Button>
                            </div>
                          )
                        })}
                        {current.members.length === 0 ? <p className="py-2 text-xs text-muted-foreground">还没有成员，点击 + 添加第一位 Agent。</p> : null}
                      </div>
                      <Separator />
                      <div className="flex items-center justify-between gap-2 p-4">
                        <Button variant="outline" size="sm" onClick={() => openEditor(current)}><Pencil data-icon="inline-start" />编辑</Button>
                        <Button variant="ghost" size="icon" aria-label="删除 Network" onClick={() => setConfirmation({ type: 'delete', network: current })}><Trash2 /></Button>
                      </div>
                    </>
                  )}
                </section>
              ) : null}

              <div className="view-controls" aria-label="画布视图控制">
                <Button variant="ghost" size="icon" aria-label="缩小画布" onClick={() => canvas.current?.zoom(.8)}><Minus /></Button>
                <output className="zoom-label" aria-label="当前缩放">{zoom}%</output>
                <Button variant="ghost" size="icon" aria-label="放大画布" onClick={() => canvas.current?.zoom(1.25)}><Plus /></Button>
                <Separator orientation="vertical" className="mx-1 min-h-5" />
                <Button variant="ghost" size="icon" aria-label="显示全部 Networks" onClick={overview}><Maximize /></Button>
                <Button variant="ghost" size="icon" aria-label="恢复节点布局" onClick={() => canvas.current?.resetLayout()}><RotateCcw /></Button>
              </div>
            </div>
          </div>
        </main>
      </div>

      <Dialog open={editor !== null} onOpenChange={open => { if (!open) setEditor(null) }}>
        <DialogContent className="max-h-[calc(100dvh-2rem)] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>{editor?.id ? '编辑 Network' : '新建 Network'}</DialogTitle>
            <DialogDescription>{editor?.id ? '修改这个 Network 的名称和用途。' : '填写名称并选择 Agent，创建后直接在画布中使用。'}</DialogDescription>
          </DialogHeader>
          <form onSubmit={saveNetwork} className="flex flex-col gap-5">
            <fieldset className="flex flex-col gap-5">
              <legend className="sr-only">Network 基本信息</legend>
              <section className="flex flex-col gap-2">
                <Label htmlFor="network-name">名称</Label>
                <Input id="network-name" required maxLength={60} value={name} onChange={event => { setName(event.target.value); setNameError(false) }} placeholder="例如：内容创作" aria-invalid={nameError} aria-describedby={nameError ? 'network-name-error' : undefined} />
                {nameError ? <p id="network-name-error" role="alert" className="text-sm text-destructive">请输入 Network 名称。</p> : null}
              </section>
              <section className="flex flex-col gap-2">
                <Label htmlFor="network-description">用途描述（选填）</Label>
                <Input id="network-description" maxLength={200} value={description} onChange={event => setDescription(event.target.value)} placeholder="这个 Network 可以帮你做什么？" />
              </section>
            </fieldset>
            {editor?.id === null ? (
              <fieldset className="flex min-w-0 flex-col gap-3">
                <legend className="mb-3 text-sm font-medium">Agent 成员（选填）</legend>
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs text-muted-foreground">可以多选，也可以先创建空 Network。</p>
                  <Badge variant="secondary" aria-live="polite">已选择 {chosen.size} 个</Badge>
                </div>
                <Input
                  type="search"
                  aria-label="搜索新 Network 的 Agent"
                  placeholder="搜索 Agent 名称或技能"
                  value={agentQuery}
                  onChange={event => setAgentQuery(event.target.value)}
                />
                <div className="flex max-h-[26dvh] flex-col gap-2 overflow-y-auto">
                  <AgentChoices candidates={candidates} chosen={chosen} onToggle={toggleChosen} />
                </div>
              </fieldset>
            ) : null}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={() => setEditor(null)}>取消</Button>
              <Button type="submit">{editor?.id ? '保存修改' : '创建 Network'}</Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      <Dialog open={picker} onOpenChange={setPicker}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>添加 Agent</DialogTitle>
            <DialogDescription>同一个 Agent 可以被多个 Network 引用，不会复制实例。以下为演示数据。</DialogDescription>
          </DialogHeader>
          <Input aria-label="搜索候选 Agent" type="search" value={agentQuery} onChange={event => setAgentQuery(event.target.value)} placeholder="搜索名称或技能" />
          <fieldset className="flex max-h-[40dvh] flex-col gap-2 overflow-y-auto">
            <legend className="sr-only">候选 Agent</legend>
            <AgentChoices
              candidates={candidates}
              chosen={chosen}
              existingIds={current?.members}
              onToggle={toggleChosen}
            />
          </fieldset>
          <DialogFooter>
            <Button variant="outline" onClick={() => setPicker(false)}>取消</Button>
            <Button disabled={chosen.size === 0} onClick={addMembers}>{chosen.size ? `添加 ${chosen.size} 个 Agent` : '添加 Agent'}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmation !== null} onOpenChange={open => { if (!open) setConfirmation(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmation?.type === 'delete' ? '删除这个 Network？' : '移除这个成员？'}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmation?.type === 'delete'
                ? `“${confirmation.network.name}”及其成员关系将被删除。Agent 本身、其他 Network 和已有 Room 不受影响。`
                : confirmation ? `从“${confirmation.network.name}”移除 ${agents.find(agent => agent.id === confirmation.agentId)?.name}。Agent 本身和其他 Network 中的引用仍保留。` : ''}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction asChild><Button variant="destructive" onClick={confirmChange}>{confirmation?.type === 'delete' ? '删除 Network' : '移除成员'}</Button></AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <Toaster
        position="bottom-center"
        style={{
          '--normal-bg': 'hsl(var(--color-popover))',
          '--normal-text': 'hsl(var(--color-popover-foreground))',
          '--normal-border': 'hsl(var(--color-border))',
        } as CSSProperties}
      />
    </>
  )
}

const root = document.getElementById('root')
if (!root) throw new Error('Network demo root is missing')
createRoot(root).render(<NetworksDemo />)
