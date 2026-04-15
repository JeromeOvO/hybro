"use client"

import type { UseCaseTemplate } from "@/lib/use-case-templates"
import { cn } from "@/lib/utils"

interface UseCaseCardProps {
  template: UseCaseTemplate
  onClick: () => void
  disabled?: boolean
}

export function UseCaseCard({ template, onClick, disabled }: UseCaseCardProps) {
  const { icon: Icon, iconGradient, title, description, agents, tag } = template

  return (
    <button
      type="button"
      onClick={disabled ? undefined : onClick}
      disabled={disabled}
      className={cn(
        "group relative overflow-hidden rounded-[18px] p-6 text-left",
        "aspect-[5/3] flex flex-col",
        "bg-gradient-to-br from-[#111122] to-[#0d0d1a]",
        "transition-all duration-250 ease-out",
        "disabled:opacity-50 disabled:cursor-not-allowed disabled:pointer-events-none",
        "hover:-translate-y-[3px]",
        "hover:shadow-[0_12px_40px_rgba(0,0,0,0.5),0_0_30px_rgba(0,255,255,0.06)]",
      )}
    >
      {/* Glassmorphism border */}
      <div
        className="pointer-events-none absolute inset-0 rounded-[18px] p-px"
        style={{
          background:
            "linear-gradient(160deg, rgba(255,255,255,0.1) 0%, rgba(255,255,255,0.02) 40%, rgba(0,255,255,0.08) 100%)",
          WebkitMask:
            "linear-gradient(#fff 0 0) content-box, linear-gradient(#fff 0 0)",
          WebkitMaskComposite: "xor",
          maskComposite: "exclude",
        }}
      />

      {/* Shimmer sweep */}
      <div
        className={cn(
          "pointer-events-none absolute -top-1/2 -left-1/2 h-[200%] w-[200%]",
          "transition-transform duration-[800ms] ease-out",
          "-translate-x-full group-hover:translate-x-[40%]",
        )}
        style={{
          background:
            "linear-gradient(115deg, transparent 30%, rgba(255,255,255,0.03) 45%, rgba(255,255,255,0.06) 50%, rgba(255,255,255,0.03) 55%, transparent 70%)",
        }}
      />

      {/* Accent glow */}
      <div
        className="pointer-events-none absolute inset-0 rounded-[18px] opacity-0 transition-opacity duration-300 group-hover:opacity-100"
        style={{
          background: `radial-gradient(ellipse at 20% 50%, ${iconGradient[0]}10 0%, transparent 70%)`,
        }}
      />

      {/* "New" corner tag */}
      {tag === "new" && (
        <span className="absolute -top-px -right-px z-10 rounded-bl-[10px] rounded-tr-[17px] border-b border-l border-cyan-500/15 bg-gradient-to-br from-cyan-400/18 to-cyan-500/10 px-2.5 py-1 text-[9px] font-bold uppercase tracking-wider text-cyan-400">
          New
        </span>
      )}

      {/* Top: Icon + Title */}
      <div className="relative z-[1] flex items-center gap-3">
        <div
          className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-[11px] shadow-[0_3px_10px_rgba(0,0,0,0.3)]"
          style={{
            background: `linear-gradient(135deg, ${iconGradient[0]}, ${iconGradient[1]})`,
          }}
        >
          <Icon className="h-[19px] w-[19px] text-white" />
        </div>
        <span className="text-[15px] font-bold leading-tight text-[#f0f0f0]">
          {title}
        </span>
      </div>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Middle: Description */}
      <p className="relative z-[1] line-clamp-2 text-[13px] leading-relaxed text-[#999]">
        {description}
      </p>

      {/* Spacer */}
      <div className="flex-1" />

      {/* Bottom: Agent avatars */}
      <div className="relative z-[1] flex items-center gap-2.5">
        <div className="flex">
          {agents.map((agent, i) => (
            <div
              key={agent.agentId}
              className={cn(
                "flex h-8 w-8 items-center justify-center rounded-full border-[2.5px] border-[#111122] shadow-[0_2px_8px_rgba(0,0,0,0.3)]",
                i > 0 && "-ml-2",
              )}
            >
              {agent.iconUrl ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={agent.iconUrl}
                  alt={agent.agentName}
                  className="h-full w-full rounded-full object-cover"
                />
              ) : (
                <span
                  data-testid="avatar-fallback"
                  className="flex h-full w-full items-center justify-center rounded-full bg-gradient-to-br from-blue-500 to-purple-500 text-xs font-bold text-white"
                >
                  {agent.agentName.charAt(0).toUpperCase()}
                </span>
              )}
            </div>
          ))}
        </div>
        <span className="text-[11px] text-[#666]">
          {agents.length} {agents.length === 1 ? "agent" : "agents"}
        </span>
      </div>
    </button>
  )
}
