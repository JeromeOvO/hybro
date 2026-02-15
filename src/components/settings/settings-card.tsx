import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { cn } from "@/lib/utils"

interface SettingsCardProps {
  title: React.ReactNode
  description: React.ReactNode
  children: React.ReactNode
  /** Extra classes on the outer <Card> (e.g. destructive border) */
  className?: string
  /** Gap between children — defaults to 6 */
  spacing?: 4 | 6
}

export function SettingsCard({
  title,
  description,
  children,
  className,
  spacing = 6,
}: SettingsCardProps) {
  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </CardHeader>
      <CardContent className={cn(spacing === 4 ? "space-y-4" : "space-y-6")}>
        {children}
      </CardContent>
    </Card>
  )
}
