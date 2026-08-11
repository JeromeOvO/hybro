/* eslint-disable react-hooks/refs -- dnd-kit intentionally exposes render-time ref-backed sortable state. */
'use client'

import * as React from 'react'
import Link from 'next/link'
import { usePathname, useRouter } from 'next/navigation'
import {
  closestCenter,
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import {
  arrayMove,
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  ChevronRight,
  CircleEllipsis,
  GripVertical,
  LoaderCircle,
  Pencil,
  Pin,
  PinOff,
  RefreshCw,
  Trash2,
} from 'lucide-react'
import { toast } from 'sonner'

import {
  deleteRoomHistoryItem,
  listRoomHistory,
  reorderPinnedRooms,
  type RoomHistoryItem,
  type RoomHistoryResponse,
  updateRoomHistoryItem,
} from '@/lib/api/room'
import { roomHistoryQueryKey } from '@/lib/room-history-query'
import { routes } from '@/lib/routes'
import { cn } from '@/lib/utils'
import {
  SidebarGroup,
  SidebarMenu,
  SidebarMenuItem,
} from '@/components/ui/sidebar'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Input } from '@/components/ui/input'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from '@/components/ui/tooltip'

const ACTIVE_STATUSES = new Set(['queued', 'processing', 'awaiting_input'])

function statusLabel(status: RoomHistoryItem['status']) {
  switch (status) {
    case 'queued': return 'Queued'
    case 'processing': return 'Working'
    case 'awaiting_input': return 'Needs input'
    default: return null
  }
}

function RoomStatus({ status }: { status: RoomHistoryItem['status'] }) {
  const label = statusLabel(status)
  if (!label) return null

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          className="inline-flex h-6 w-5 shrink-0 items-center justify-center"
          aria-label={label}
        >
          {status === 'awaiting_input' ? (
            <span className="h-2 w-2 rounded-full bg-amber-400 ring-2 ring-amber-400/15" />
          ) : status === 'processing' ? (
            <span className="h-2 w-2 rounded-full bg-cyan-400 motion-safe:animate-pulse" />
          ) : (
            <span className="h-2 w-2 rounded-full bg-muted-foreground" />
          )}
        </span>
      </TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  )
}

interface HistoryRowProps {
  room: RoomHistoryItem
  active: boolean
  editing: boolean
  onStartRename: () => void
  onRename: (title: string) => void
  onTogglePin: () => void
  onDelete: () => void
  dragEnabled?: boolean
}

function HistoryRow({
  room,
  active,
  editing,
  onStartRename,
  onRename,
  onTogglePin,
  onDelete,
  dragEnabled = false,
}: HistoryRowProps) {
  const [title, setTitle] = React.useState(room.title)
  const inputRef = React.useRef<HTMLInputElement>(null)
  const sortable = useSortable({ id: room.room_id, disabled: !dragEnabled })

  React.useEffect(() => setTitle(room.title), [room.title])
  React.useEffect(() => {
    if (editing) {
      inputRef.current?.focus()
      inputRef.current?.select()
    }
  }, [editing])

  const commitRename = () => {
    const normalized = title.trim()
    if (!normalized || normalized === room.title) {
      setTitle(room.title)
      onRename(room.title)
      return
    }
    onRename(normalized)
  }

  return (
    <div
      ref={sortable.setNodeRef}
      style={{
        transform: CSS.Transform.toString(sortable.transform),
        transition: sortable.transition,
      }}
      className={cn(
        'group/history-row flex h-9 items-center rounded-md text-[0.9rem] outline-none transition-colors',
        active
          ? 'bg-black/15 font-medium text-sidebar-accent-foreground dark:bg-white/15'
          : 'hover:bg-black/10 dark:hover:bg-white/10',
        sortable.isDragging && 'z-20 bg-sidebar-accent opacity-80 shadow-md',
      )}
      data-testid={`history-room-${room.room_id}`}
    >
      {dragEnabled ? (
        <>
          <span className="w-2 shrink-0 md:hidden" />
          <button
            type="button"
            className="ml-1 hidden h-7 w-5 cursor-grab items-center justify-center rounded text-muted-foreground opacity-0 outline-none transition-opacity hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-sidebar-ring active:cursor-grabbing group-hover/history-row:opacity-100 md:inline-flex"
            aria-label={`Reorder ${room.title}`}
            {...sortable.attributes}
            {...sortable.listeners}
          >
            <GripVertical className="h-3.5 w-3.5" />
          </button>
        </>
      ) : (
        <span className="w-2 shrink-0" />
      )}

      {editing ? (
        <Input
          ref={inputRef}
          value={title}
          maxLength={80}
          onChange={(event) => setTitle(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') commitRename()
            if (event.key === 'Escape') {
              setTitle(room.title)
              onRename(room.title)
            }
          }}
          onBlur={commitRename}
          className="mr-1 h-7 min-w-0 flex-1 px-2 text-sm"
          aria-label="Room name"
        />
      ) : (
        <Link
          href={routes.room(room.room_id)}
          prefetch={false}
          scroll={false}
          aria-current={active ? 'page' : undefined}
          className="flex min-w-0 flex-1 items-center self-stretch pl-2 outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-sidebar-ring"
          title={room.title}
        >
          <span className="truncate">{room.title}</span>
        </Link>
      )}

      {!editing ? <RoomStatus status={room.status} /> : null}

      {!editing ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button
              type="button"
              className="mr-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-muted-foreground opacity-0 outline-none transition-opacity hover:bg-black/10 hover:text-foreground focus-visible:opacity-100 focus-visible:ring-2 focus-visible:ring-sidebar-ring data-[state=open]:opacity-100 group-hover/history-row:opacity-100 dark:hover:bg-white/10"
              aria-label={`Actions for ${room.title}`}
            >
              <CircleEllipsis className="h-4 w-4" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="right" align="start" className="w-40">
            <DropdownMenuItem onSelect={onTogglePin}>
              {room.is_pinned ? <PinOff /> : <Pin />}
              {room.is_pinned ? 'Unpin' : 'Pin'}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={onStartRename}>
              <Pencil />
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem variant="destructive" onSelect={onDelete}>
              <Trash2 />
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
    </div>
  )
}

export function ChatHistory({ enabled, userId }: { enabled: boolean; userId: string }) {
  const pathname = usePathname()
  const router = useRouter()
  const queryClient = useQueryClient()
  const queryKey = roomHistoryQueryKey(userId)
  const [open, setOpen] = React.useState(true)
  const [editingId, setEditingId] = React.useState<string | null>(null)
  const [deleteTarget, setDeleteTarget] = React.useState<RoomHistoryItem | null>(null)
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  )

  const query = useQuery({
    queryKey,
    queryFn: ({ signal }) => listRoomHistory(undefined, signal),
    enabled,
    staleTime: 10_000,
    refetchOnWindowFocus: true,
    refetchInterval: (currentQuery) => {
      const data = currentQuery.state.data as RoomHistoryResponse | undefined
      return data?.items.some((item) => ACTIVE_STATUSES.has(item.status)) ? 10_000 : false
    },
  })

  const useOptimisticMutation = <TVariables,>(
    mutationFn: (variables: TVariables) => Promise<unknown>,
    update: (data: RoomHistoryResponse, variables: TVariables) => RoomHistoryResponse,
    errorMessage: string,
  ) => useMutation({
    mutationFn,
    onMutate: async (variables) => {
      await queryClient.cancelQueries({ queryKey })
      const previous = queryClient.getQueryData<RoomHistoryResponse>(queryKey)
      if (previous) {
        queryClient.setQueryData(queryKey, update(previous, variables))
      }
      return { previous }
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous)
      toast.error(errorMessage)
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  })

  const renameMutation = useOptimisticMutation(
    ({ roomId, title }: { roomId: string; title: string }) => updateRoomHistoryItem(roomId, { title }),
    (data, variables) => ({
      items: data.items.map((item) => item.room_id === variables.roomId ? { ...item, title: variables.title } : item),
    }),
    'Could not rename the conversation',
  )

  const pinMutation = useOptimisticMutation(
    ({ roomId, pinned }: { roomId: string; pinned: boolean }) => updateRoomHistoryItem(roomId, { is_pinned: pinned }),
    (data, variables) => {
      const maxOrder = Math.max(0, ...data.items.filter((item) => item.is_pinned).map((item) => item.pin_order ?? 0))
      return {
        items: data.items.map((item) => item.room_id === variables.roomId
          ? { ...item, is_pinned: variables.pinned, pin_order: variables.pinned ? maxOrder + 1 : null }
          : item),
      }
    },
    'Could not update the pinned conversation',
  )

  const deleteMutation = useOptimisticMutation(
    (roomId: string) => deleteRoomHistoryItem(roomId),
    (data, roomId) => ({ items: data.items.filter((item) => item.room_id !== roomId) }),
    'Could not delete the conversation',
  )

  const reorderMutation = useOptimisticMutation(
    (roomIds: string[]) => reorderPinnedRooms(roomIds),
    (data, roomIds) => {
      const order = new Map(roomIds.map((id, index) => [id, index + 1]))
      return {
        items: data.items.map((item) => order.has(item.room_id) ? { ...item, pin_order: order.get(item.room_id)! } : item),
      }
    },
    'Could not save the pinned order',
  )

  const items = query.data?.items ?? []
  const pinned = items
    .filter((item) => item.is_pinned)
    .toSorted((a, b) => (a.pin_order ?? Number.MAX_SAFE_INTEGER) - (b.pin_order ?? Number.MAX_SAFE_INTEGER))
  const recent = items
    .filter((item) => !item.is_pinned)
    .toSorted((a, b) => Date.parse(b.last_activity_at) - Date.parse(a.last_activity_at))

  const renderRow = (room: RoomHistoryItem, dragEnabled = false) => (
    <HistoryRow
      key={room.room_id}
      room={room}
      dragEnabled={dragEnabled && !reorderMutation.isPending}
      active={pathname === routes.room(room.room_id)}
      editing={editingId === room.room_id}
      onStartRename={() => setEditingId(room.room_id)}
      onRename={(title) => {
        setEditingId(null)
        if (title !== room.title) renameMutation.mutate({ roomId: room.room_id, title })
      }}
      onTogglePin={() => pinMutation.mutate({ roomId: room.room_id, pinned: !room.is_pinned })}
      onDelete={() => setDeleteTarget(room)}
    />
  )

  const onDragEnd = ({ active, over }: DragEndEvent) => {
    if (reorderMutation.isPending || !over || active.id === over.id) return
    const oldIndex = pinned.findIndex((item) => item.room_id === active.id)
    const newIndex = pinned.findIndex((item) => item.room_id === over.id)
    if (oldIndex < 0 || newIndex < 0) return
    reorderMutation.mutate(arrayMove(pinned, oldIndex, newIndex).map((item) => item.room_id))
  }

  return (
    <Collapsible open={open} onOpenChange={setOpen} asChild>
      <SidebarGroup
        className={cn(
          'min-h-0 overflow-hidden px-2 pt-0',
          open ? 'flex-1' : 'flex-none',
        )}
      >
        <CollapsibleTrigger asChild>
          <button
            type="button"
            className="flex h-9 w-full shrink-0 items-center gap-2 rounded-md px-2 text-left text-sm font-medium outline-none hover:bg-sidebar-accent focus-visible:ring-2 focus-visible:ring-sidebar-ring"
          >
            <span>Chat History</span>
            <span className="ml-auto flex items-center gap-1.5">
              {query.isFetching ? (
                <RefreshCw
                  className="h-3.5 w-3.5 animate-spin text-muted-foreground"
                  aria-label="Refreshing history"
                />
              ) : null}
              <ChevronRight
                className={cn(
                  'h-4 w-4 text-muted-foreground transition-transform duration-200',
                  open && 'rotate-90',
                )}
              />
            </span>
          </button>
        </CollapsibleTrigger>

        <CollapsibleContent className="min-h-0 flex-1 overflow-hidden">
          <div className="h-full overflow-y-auto pb-2">
        {query.isPending ? (
          <div className="space-y-2 px-2 py-2" role="status" aria-label="Loading chat history">
            {[0, 1, 2, 3].map((value) => <div key={value} className="h-7 animate-pulse rounded bg-sidebar-accent/60" />)}
          </div>
        ) : query.isError ? (
          <button
            type="button"
            onClick={() => query.refetch()}
            className="mx-2 flex w-[calc(100%-1rem)] items-center gap-2 rounded-md px-2 py-2 text-left text-xs text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Couldn&apos;t load history. Retry
          </button>
        ) : items.length === 0 ? (
          <p className="px-4 py-3 text-xs text-muted-foreground">No conversations yet</p>
        ) : (
          <SidebarMenu className="gap-0.5">
            {pinned.length > 0 ? (
              <SidebarMenuItem>
                <div className="px-3 pb-1 pt-2 text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">Pinned</div>
                <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
                  <SortableContext items={pinned.map((item) => item.room_id)} strategy={verticalListSortingStrategy}>
                    <div className="space-y-0.5">{pinned.map((room) => renderRow(room, true))}</div>
                  </SortableContext>
                </DndContext>
              </SidebarMenuItem>
            ) : null}
            {recent.length > 0 ? (
              <SidebarMenuItem>
                {pinned.length > 0 ? <div className="px-3 pb-1 pt-3 text-[0.68rem] font-medium uppercase tracking-wider text-muted-foreground">Recent</div> : null}
                <div className="space-y-0.5">{recent.map((room) => renderRow(room))}</div>
              </SidebarMenuItem>
            ) : null}
          </SidebarMenu>
        )}
          </div>
        </CollapsibleContent>

      <AlertDialog open={deleteTarget !== null} onOpenChange={(open) => { if (!open) setDeleteTarget(null) }}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete conversation?</AlertDialogTitle>
            <AlertDialogDescription>
              “{deleteTarget?.title}” and its related files will be permanently deleted. This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              disabled={deleteMutation.isPending}
              onClick={() => {
                if (!deleteTarget) return
                const roomId = deleteTarget.room_id
                setDeleteTarget(null)
                deleteMutation.mutate(roomId, {
                  onSuccess: () => {
                    toast.success('Conversation deleted')
                    if (pathname === routes.room(roomId)) router.push(routes.chat)
                  },
                })
              }}
            >
              {deleteMutation.isPending ? <LoaderCircle className="mr-2 h-4 w-4 animate-spin" /> : null}
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
        </AlertDialog>
      </SidebarGroup>
    </Collapsible>
  )
}
