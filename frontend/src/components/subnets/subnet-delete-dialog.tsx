'use client'

import { Alert, AlertDescription } from '@/components/ui/alert'
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { Button } from '@/components/ui/button'
import type { AgentGroup } from '@/lib/types/agent-group'

export interface SubnetDeleteDialogProps {
  subnet: AgentGroup | null
  pending: boolean
  error: string | null
  onCancel: () => void
  onConfirm: () => void
}

export function SubnetDeleteDialog({ subnet, pending, error, onCancel, onConfirm }: SubnetDeleteDialogProps) {
  return (
    <AlertDialog open={subnet !== null} onOpenChange={open => { if (!open && !pending) onCancel() }}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this subnet?</AlertDialogTitle>
          <AlertDialogDescription>
            Delete “{subnet?.name}” and its memberships. Agents, other subnets, and existing rooms are kept.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? <Alert variant="destructive"><AlertDescription>{error}</AlertDescription></Alert> : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction asChild>
            <Button variant="dangerSolid" disabled={pending} onClick={event => { event.preventDefault(); onConfirm() }}>
              {pending ? 'Deleting…' : 'Delete subnet'}
            </Button>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
