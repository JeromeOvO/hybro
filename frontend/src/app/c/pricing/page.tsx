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

// Removed unused formatUsd function

function calcYearlyFromMonthly(monthly: number) {
  return Math.round(monthly * 12 * 0.75 * 100) / 100 // 25% off
}

function PricingClient() {

  const [billing, setBilling] = useState<BillingCycle>("monthly")

  const plans = useMemo<Plan[]>(
    () => [
      {
        id: "basic",
        name: "Basic",
        description: "Perfect for getting started with AI agents",
        highlights: ["Access to Agent Network", "Basic Agent Interactions", "Community Support"],
        monthlyPrice: 0
      },
      {
        id: "pro",
        name: "Pro",
        description: "For professionals requiring advanced capabilities",
        highlights: ["Everything in Basic", "Advanced Agent Infrastructure", "Priority Support", "Unlimited Agent Interactions", "Custom Agent Deployment"],
        featured: true,
        monthlyPrice: 9.99
      },
      {
        id: "enterprise",
        name: "Enterprise",
        description: "For large teams and organizations",
        highlights: [
          "Everything in Pro",
          "Dedicated Infrastructure",
          "Custom SLA & Support",
          "SSO & Advanced Security",
          "On-premise Deployment Options"
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
    if (plan.id === "enterprise") {
      return (
        <div className="h-20 flex flex-col items-center justify-center text-center">
          <div className="text-3xl font-extrabold tracking-tight">{plan.priceLabel ?? "Contact us"}</div>
          <div className="text-sm text-muted-foreground mt-1">Tailored for you</div>
        </div>
      )
    }

    const monthly = plan.monthlyPrice ?? 0
    const yearly = getYearly(plan)
    const perMo = yearly / 12

    const main = billing === "monthly" ? monthly : perMo

    return (
      <div className="h-20 flex flex-col items-center justify-center text-center">
        <div className="flex items-start justify-center gap-1">
          <span className="text-2xl font-bold mt-1">$</span>
          <span className="text-6xl font-extrabold leading-none tracking-tight">{main.toFixed(2)}</span>
        </div>
        <div className="text-sm font-medium text-muted-foreground mt-2">
          per month, {billing === "yearly" ? "billed yearly" : "billed monthly"}
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen py-16 bg-gradient-to-b from-background to-background/50">
      <div className="max-w-7xl mx-auto space-y-16 px-4 sm:px-8">
        {/* Header Section */}
        <div className="text-center space-y-6 max-w-3xl mx-auto">
          <h1 className="text-5xl sm:text-7xl font-extrabold tracking-tight leading-tight">
            Simple, Transparent <br />
            <span className="text-transparent bg-clip-text bg-gradient-to-r from-[hsl(var(--color-hybro-hy))] to-[hsl(var(--color-hybro-bro))]">
              Pricing Plans
            </span>
          </h1>
          <p className="text-xl text-muted-foreground leading-relaxed">
            Choose the perfect plan to unlock the full potential of your AI Agent Network.
            Scale seamlessly as your needs grow.
          </p>
        </div>

        {/* Billing Toggle */}
        <div className="flex justify-center">
          <div className="relative inline-flex bg-muted/50 rounded-full p-1.5 border border-border/50">
            <div
              className={cn(
                "absolute inset-y-1.5 rounded-full bg-background shadow-sm transition-all duration-300 ease-in-out",
                billing === "monthly" ? "left-1.5 right-1/2" : "left-1/2 right-1.5"
              )}
            />
            <button
              onClick={() => setBilling("monthly")}
              className={cn(
                "relative z-10 w-32 py-2.5 text-sm font-semibold rounded-full transition-colors duration-200",
                billing === "monthly" ? "text-foreground" : "text-muted-foreground hover:text-foreground"
              )}
            >
              Monthly
            </button>
            <button
              onClick={() => setBilling("yearly")}
              className={cn(
                "relative z-10 w-40 py-2.5 text-sm font-semibold rounded-full transition-colors duration-200 flex items-center justify-center gap-2",
                billing === "yearly" ? "text-foreground" : "text-muted-foreground hover:text-foreground"
              )}
            >
              Yearly
              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-green-100 text-green-700 dark:bg-green-900/30 dark:text-green-400">
                -25%
              </span>
            </button>
          </div>
        </div>

        {/* Pricing Cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-8 items-start">
          {plans.map((plan) => {
            const isFeatured = !!plan.featured
            const highlightColor = isFeatured
              ? "text-[hsl(var(--color-hybro-hy))]"
              : "text-primary"

            return (
              <div
                key={plan.id}
                className={cn(
                  "relative flex flex-col rounded-3xl p-8 transition-all duration-300",
                  "bg-background/40 backdrop-blur-xl border",
                  isFeatured
                    ? "border-[hsl(var(--color-hybro-hy))/30] shadow-2xl shadow-[hsl(var(--color-hybro-hy))/10] scale-105 z-10 ring-1 ring-[hsl(var(--color-hybro-hy))/20]"
                    : "border-border/50 shadow-lg hover:shadow-xl hover:-translate-y-1 hover:border-border/80",
                )}
              >
                {isFeatured && (
                  <div className="absolute -top-4 left-1/2 -translate-x-1/2 px-4 py-1.5 rounded-full bg-gradient-to-r from-[hsl(var(--color-hybro-hy))] to-[hsl(var(--color-hybro-bro))] text-white text-xs font-bold uppercase tracking-wider shadow-lg">
                    Most Popular
                  </div>
                )}

                <div className="text-center space-y-4 mb-8">
                  <h3 className="text-2xl font-bold">{plan.name}</h3>
                  <div className="text-sm text-muted-foreground min-h-[40px] flex items-center justify-center">
                    {plan.description}
                  </div>
                </div>

                <div className="mb-8 p-6 rounded-2xl bg-muted/30 border border-border/20">
                  {renderPrice(plan)}
                </div>

                <div className="flex-1 space-y-4 mb-8">
                  <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground mb-4">
                    What&apos;s included
                  </div>
                  {plan.highlights.map((h) => (
                    <div key={h} className="flex items-start gap-3 text-sm">
                      <div className={cn("mt-0.5 p-0.5 rounded-full bg-background/80 shadow-sm", highlightColor)}>
                        <Check className="h-3.5 w-3.5 stroke-[3]" />
                      </div>
                      <span className="text-foreground/80 leading-tight">{h}</span>
                    </div>
                  ))}
                </div>

                <button
                  className={cn(
                    "w-full py-3 px-6 rounded-xl text-sm font-semibold transition-all duration-200 shadow-sm",
                    isFeatured
                      ? "bg-gradient-to-r from-[hsl(var(--color-hybro-hy))] to-[hsl(var(--color-hybro-bro))] text-white hover:shadow-lg hover:opacity-90 hover:scale-[1.02]"
                      : "bg-white dark:bg-white/10 text-foreground border border-border/50 hover:bg-muted/50 hover:border-foreground/20"
                  )}
                >
                  {plan.id === "enterprise" ? "Contact Sales" : "Get Started"}
                </button>
              </div>
            )
          })}
        </div>

        {/* Footer Contact */}
        <div className="max-w-2xl mx-auto text-center py-12 px-4 rounded-3xl bg-muted/20 border border-border/40 backdrop-blur-sm">
          <h3 className="text-2xl font-bold mb-3">
            Need a custom solution?
          </h3>
          <p className="text-muted-foreground mb-6">
            Contact us for enterprise-grade features, dedicated support, and custom deployment options.
          </p>
          <a
            href="mailto:info@hybro.ai"
            className="inline-flex items-center gap-2 text-primary font-medium hover:underline underline-offset-4"
          >
            <Mail className="h-4 w-4" />
            info@hybro.ai
          </a>
        </div>
      </div>
    </div>
  )
}

function PricingSkeleton() {
  return (
    <div className="py-8">
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
