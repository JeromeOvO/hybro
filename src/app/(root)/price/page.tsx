"use client"

import { Suspense, useMemo, useState } from "react"
import { Check, Mail } from "lucide-react"

type BillingCycle = "monthly" | "yearly"
type PlanId = "basic" | "pro" | "enterprise"

type Plan = {
  id: PlanId
  name: string
  description: string
  highlights: string[]
  featured?: boolean
  monthlyPrice?: number
  yearlyPrice?: number
  priceLabel?: string
}

function cn(...classes: Array<string | false | null | undefined>) {
  return classes.filter(Boolean).join(" ")
}

function formatUsd(n: number) {
  return `$${n.toFixed(2)}`
}

function calcYearlyFromMonthly(monthly: number) {
  return Math.round(monthly * 12 * 0.75 * 100) / 100 // 25% off
}

function PricingClient() {

  const [billing, setBilling] = useState<BillingCycle>("monthly")
  const [selected, setSelected] = useState<PlanId>("pro")

  const plans = useMemo<Plan[]>(
    () => [
      {
        id: "basic",
        name: "Basic",
        description: "For Basic Users",
        highlights: ["Agent Network Services"],
        monthlyPrice: 0
      },
      {
        id: "pro",
        name: "Pro",
        description: "For Professional Users",
        highlights: ["Agent Network Services", "Agent Infrastructure Services", "Priority Support"],
        featured: true,
        monthlyPrice: 9.99
      },
      {
        id: "enterprise",
        name: "Enterprise",
        description: "For Enterprise & Team",
        highlights: [
          "Agent Network Services",
          "Agent Infrastructure Services",
          "Customized pricing",
          "SLA & support",
          "Security and compliance options"
        ],
        priceLabel: "Contact us"
      }
    ],
    []
  )

  const getYearly = (plan: Plan) => {
    const monthly = plan.monthlyPrice ?? 0
    return plan.yearlyPrice ?? calcYearlyFromMonthly(monthly)
  }

  const renderPrice = (plan: Plan) => {
    const shell = "h-24 flex flex-col items-center justify-center text-center"

    if (plan.id === "enterprise") {
      return (
        <div className={shell}>
          <div className="text-2xl font-extrabold tracking-tight">{plan.priceLabel ?? "Contact us"}</div>
          <div className="text-xs text-muted-foreground opacity-0">placeholder</div>
        </div>
      )
    }

    const monthly = plan.monthlyPrice ?? 0
    const yearly = getYearly(plan)
    const perMo = yearly / 12

    const main = billing === "monthly" ? monthly : perMo

    return (
      <div className={shell}>
        <div className="flex items-end gap-1">
          <span className="text-xl font-bold">$</span>
          <span className="text-5xl font-extrabold leading-none">{main.toFixed(2)}</span>
          <span className="text-sm font-semibold text-muted-foreground">/mo</span>
        </div>

        <div
          className={cn(
            "text-xs text-muted-foreground mt-1 transition-opacity",
            billing === "yearly" ? "opacity-100" : "opacity-0"
          )}
          aria-hidden={billing !== "yearly"}
        >
          {formatUsd(yearly)} billed yearly
        </div>
      </div>
    )
  }

  return (
    <div className="px-4 sm:px-6 py-8">
      <div className="w-full max-w-6xl mx-auto space-y-8">
        <div className="text-center space-y-3">
          <h1 className="text-5xl sm:text-6xl font-extrabold tracking-tight">Pricing Plan</h1>
        </div>

        <div className="flex justify-center">
          <div className="w-full max-w-2xl rounded-full border border-border/50 bg-background/30 backdrop-blur-md p-2">
            <div className="grid grid-cols-2 gap-2">
              <button
                onClick={() => setBilling("monthly")}
                className={cn(
                  "h-16 rounded-full text-xl font-semibold transition-all",
                  billing === "monthly"
                    ? "bg-black text-white shadow-lg"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                Monthly
              </button>
              <button
                onClick={() => setBilling("yearly")}
                className={cn(
                  "h-16 rounded-full text-xl font-semibold transition-all flex items-center justify-center gap-3",
                  billing === "yearly"
                    ? "bg-black text-white shadow-lg"
                    : "text-muted-foreground hover:text-foreground"
                )}
              >
                Yearly <span className="text-green-600 font-semibold">(25% off)</span>
              </button>
            </div>
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {plans.map((plan) => {
            const isSelected = selected === plan.id
            const isFeatured = !!plan.featured

            return (
              <button
                key={plan.id}
                type="button"
                onClick={() => setSelected(plan.id)}
                className={cn(
                  "relative rounded-2xl p-5 text-left flex flex-col",
                  "bg-background/60 backdrop-blur-md border border-border/50",
                  "transition-all duration-200 ease-out",
                  "hover:-translate-y-0.5 hover:scale-[1.02]",
                  "hover:shadow-[0_0_0_1px_rgba(255,255,255,0.18),0_0_40px_rgba(255,255,255,0.10)]",
                  isSelected && "ring-2 ring-primary/30",
                  isFeatured && "border-primary/30"
                )}
              >
                <div className="text-lg font-semibold">{plan.name}</div>
                <div className="mt-4">{renderPrice(plan)}</div>

                <div className="mt-5 h-px bg-border/60" />

                <div className="mt-5 space-y-2">
                  {plan.highlights.map((h) => (
                    <div key={h} className="flex items-center gap-2 text-sm">
                      <Check className="h-4 w-4 text-primary" />
                      <span className="text-muted-foreground">{h}</span>
                    </div>
                  ))}
                </div>

                <div className="mt-5 text-sm text-muted-foreground">{plan.description}</div>
              </button>
            )
          })}
        </div>

        <footer className="py-12 px-4 bg-muted/30">
        <div className="max-w-6xl mx-auto">
          <div className="text-center">
            <div className="mb-6">
              <h3 className="text-2xl font-bold mb-2">
                <span className="text-[hsl(var(--color-hybro-hy))]">Contact Us For </span>
                <span className="text-[hsl(var(--color-hybro-bro))]">Upgrade & Support</span>
              </h3>
            </div>
            <div className="flex items-center justify-center gap-2 mb-4">
              <Mail className="h-5 w-5 icon-contact" />
              <a 
                href="mailto:info@hybro.ai" 
                className="text-muted-foreground hover:text-primary transition-colors"
              >
                info@hybro.ai
              </a>
            </div>
          </div>
        </div>
      </footer>
      </div>
    </div>
  )
}

function PricingSkeleton() {
  return (
    <div className="px-4 sm:px-6 py-8">
      <div className="w-full max-w-6xl mx-auto space-y-6">
        <div className="h-14 w-72 mx-auto rounded-md bg-muted/50 animate-pulse" />
        <div className="h-10 w-[520px] max-w-full mx-auto rounded-md bg-muted/40 animate-pulse" />
        <div className="h-20 rounded-full bg-muted/40 animate-pulse" />
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          <div className="h-[320px] rounded-2xl bg-muted/40 animate-pulse" />
          <div className="h-[320px] rounded-2xl bg-muted/40 animate-pulse" />
          <div className="h-[320px] rounded-2xl bg-muted/40 animate-pulse" />
        </div>
        <div className="h-16 rounded-2xl bg-muted/40 animate-pulse" />
      </div>
    </div>
  )
}

export default function PricingPage() {
  return (
    <Suspense fallback={<PricingSkeleton />}>
      <PricingClient />
    </Suspense>
  )
}
