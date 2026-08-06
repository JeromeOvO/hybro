'use client'

import { useEffect, useState } from "react"

type Line = { kind: "cmd" | "ok" | "info"; text: string }

const LINES: Line[] = [
  { kind: "cmd", text: "curl -fsSL hybro.ai/install.sh | sh" },
  { kind: "ok", text: "cloning hybroai/hybro" },
  { kind: "ok", text: "backend/.env created" },
  { kind: "ok", text: "docker compose up -d --build" },
  { kind: "info", text: "Hybro App    http://localhost:3000" },
  { kind: "info", text: "API Server   http://localhost:8000" },
]

const TYPE_MS = 26
const LINE_PAUSE_MS = 260
const LOOP_PAUSE_MS = 5200

export function TypingTerminal() {
  const [lineIndex, setLineIndex] = useState(0)
  const [charIndex, setCharIndex] = useState(0)

  useEffect(() => {
    const done = lineIndex >= LINES.length

    if (done) {
      const restart = setTimeout(() => {
        setLineIndex(0)
        setCharIndex(0)
      }, LOOP_PAUSE_MS)
      return () => clearTimeout(restart)
    }

    const current = LINES[lineIndex]
    if (charIndex < current.text.length) {
      const t = setTimeout(() => setCharIndex((c) => c + 1), TYPE_MS)
      return () => clearTimeout(t)
    }

    const t = setTimeout(() => {
      setLineIndex((l) => l + 1)
      setCharIndex(0)
    }, LINE_PAUSE_MS)
    return () => clearTimeout(t)
  }, [lineIndex, charIndex])

  return (
    <div className="relative rounded-2xl border border-border/50 bg-[hsl(var(--color-background))]/60 p-5 md:p-6 font-mono text-[12px] md:text-[13px] leading-[1.9] overflow-hidden">
      <div className="relative min-h-[170px]">
        {LINES.map((line, i) => {
          if (i > lineIndex) return null
          const text = i === lineIndex ? line.text.slice(0, charIndex) : line.text
          const active = i === lineIndex

          return (
            <div key={line.text} className="whitespace-pre-wrap break-all">
              <span className="select-none">
                {line.kind === "cmd" && <span className="text-[hsl(var(--color-hybro-bro))]">$ </span>}
                {line.kind === "ok" && <span className="text-emerald-500 dark:text-emerald-400">✓ </span>}
                {line.kind === "info" && <span className="text-[hsl(var(--color-hybro-hy))]">→ </span>}
              </span>
              <span
                className={
                  line.kind === "cmd"
                    ? "text-foreground"
                    : line.kind === "info"
                      ? "text-[hsl(var(--color-hybro-hy))]"
                      : "text-muted-foreground"
                }
              >
                {text}
              </span>
              {active && <span className="ml-0.5 inline-block w-[7px] h-[1.05em] translate-y-[0.18em] bg-[hsl(var(--color-hybro-hy))]" />}
            </div>
          )
        })}
      </div>
    </div>
  )
}
