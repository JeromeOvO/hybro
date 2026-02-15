import { Label } from "@/components/ui/label"

interface FormGroupProps {
  /** The `id` shared by the label's `htmlFor` and the input */
  id: string
  label: React.ReactNode
  hint?: React.ReactNode
  children: React.ReactNode
}

export function FormGroup({ id, label, hint, children }: FormGroupProps) {
  return (
    <div className="space-y-2">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
    </div>
  )
}
