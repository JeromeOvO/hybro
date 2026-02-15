import { Label } from "@/components/ui/label"

interface FormGroupProps {
  /** The `id` shared by the label's `htmlFor` and the input */
  id: string
  label: React.ReactNode
  hint?: React.ReactNode
  /** Validation error — shown in destructive color, replaces the hint when present */
  error?: React.ReactNode
  children: React.ReactNode
}

export function FormGroup({ id, label, hint, error, children }: FormGroupProps) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {error && <p className="text-xs text-destructive">{error}</p>}
      {!error && hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}
