'use client'

import React, { useCallback, useEffect, useRef, useState } from 'react'
import { Streamdown } from 'streamdown'
import rehypeHighlight from 'rehype-highlight'
import { Check, ChevronRight, Code2, Copy } from 'lucide-react'
import { cn, formatIfJson } from '@/lib/utils'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from './ui/collapsible'

/**
 * Extract plain text from React children tree (strips HTML / highlight spans).
 */
function extractText(node: React.ReactNode): string {
  if (typeof node === 'string') return node
  if (typeof node === 'number') return String(node)
  if (!node) return ''
  if (Array.isArray(node)) return node.map(extractText).join('')
  if (typeof node === 'object' && 'props' in node) {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    return extractText((node as React.ReactElement<any>).props.children)
  }
  return ''
}

/**
 * Code block with a copy-to-clipboard button in the top-right corner.
 */
function CodeBlockWithCopy({
  className,
  children,
  ...props
}: React.HTMLAttributes<HTMLElement> & { children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const handleCopy = useCallback(() => {
    const text = extractText(children).replace(/\n$/, '')
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      if (timerRef.current) clearTimeout(timerRef.current)
      timerRef.current = setTimeout(() => setCopied(false), 2000)
    })
  }, [children])

  return (
    <div className="relative group">
      <pre className="max-w-full bg-slate-100 dark:bg-slate-900 text-slate-700 dark:text-slate-200 p-3 pr-10 rounded-md overflow-x-auto border border-slate-200 dark:border-slate-700">
        <code className={className} {...props}>
          {children}
        </code>
      </pre>
      <button
        type="button"
        onClick={handleCopy}
        aria-label={copied ? 'Copied' : 'Copy'}
        className="absolute top-2 right-2 p-1 rounded-md text-slate-500 dark:text-slate-400 hover:text-slate-700 dark:hover:text-slate-200 hover:bg-slate-200 dark:hover:bg-slate-700 transition-colors duration-150"
      >
        {copied ? (
          <Check className="h-4 w-4 text-green-500" />
        ) : (
          <Copy className="h-4 w-4" />
        )}
      </button>
    </div>
  )
}

/**
 * Context that propagates the parent message bubble's expanded state.
 * When true, JSON code blocks inside the message should default to open.
 */
export const JsonBlockExpandedContext = React.createContext(false)

/**
 * Collapsible wrapper for JSON fenced code blocks in markdown.
 * Starts closed so large JSON responses don't flood the message view,
 * but opens automatically when the parent message bubble is expanded.
 */
function CollapsibleCodeBlock({
  className,
  children,
  lineCount,
}: React.HTMLAttributes<HTMLElement> & { children?: React.ReactNode; lineCount: number }) {
  const isMessageExpanded = React.useContext(JsonBlockExpandedContext)
  const [open, setOpen] = useState(isMessageExpanded)

  useEffect(() => {
    if (isMessageExpanded) setOpen(true)
  }, [isMessageExpanded])

  return (
    <Collapsible open={open} onOpenChange={setOpen} className="my-1">
      <CollapsibleTrigger className="inline-flex cursor-pointer items-center gap-1 py-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors">
        <ChevronRight
          className="h-3.5 w-3.5 transition-transform duration-150 ease-in-out"
          style={{ transform: open ? 'rotate(90deg)' : 'rotate(0deg)' }}
        />
        <Code2 className="h-3.5 w-3.5" />
        <span>JSON</span>
        <span className="text-muted-foreground/60">
          · {lineCount} {lineCount === 1 ? 'line' : 'lines'}
        </span>
      </CollapsibleTrigger>
      <CollapsibleContent className="data-[state=open]:animate-collapsible-down overflow-hidden">
        <div className="mt-1">
          <CodeBlockWithCopy className={className}>{children}</CodeBlockWithCopy>
        </div>
      </CollapsibleContent>
    </Collapsible>
  )
}


function processMentions(content: string): string {
  return content.replace(
    /<@([^|]+)\|([^>]+)>/g,
    '[@$2](/c/agents/$1)'
  )
}

/** Check if a link href points to an agent profile (i.e. was an @mention). */
function isAgentMentionHref(href: string | undefined): boolean {
  return !!href && href.startsWith('/c/agents/')
}

/** Shared custom component overrides used by all Streamdown instances. */
function makeComponents(isStreaming: boolean) {
  return {
  a: ({ children, href, ...props }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { children?: React.ReactNode }) => {
    if (isAgentMentionHref(href)) {
      return (
        <a
          className="prose room-mention mx-1 hover:underline underline-offset-2 transition-opacity hover:opacity-80"
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          {...props}
        >
          {children}
        </a>
      )
    }
    return (
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:text-blue-800 dark:hover:text-blue-300 hover:underline underline-offset-2 transition-colors duration-150"
        {...props}
      >
        {children}
      </a>
    )
  },
  code: ({ className, children, ...props }: React.HTMLAttributes<HTMLElement> & { children?: React.ReactNode }) => {
    const match = /language-(\w+)/.exec(className || '')
    const isInline = !match
    if (isInline) {
      return (
        <code
          className="bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 px-1.5 py-0.5 rounded text-sm font-mono"
          {...props}
        >
          {children}
        </code>
      )
    }
    const isJson = match[1] === 'json'
    if (isJson && !isStreaming) {
      const text = extractText(children)
      const lineCount = text.split('\n').filter((l, i, a) => i < a.length - 1 || l.trim()).length
      return (
        <CollapsibleCodeBlock className={className} lineCount={lineCount}>
          {children}
        </CollapsibleCodeBlock>
      )
    }
    return (
      <CodeBlockWithCopy className={className} {...props}>
        {children}
      </CodeBlockWithCopy>
    )
  },
  p: ({ children }: { children?: React.ReactNode }) => <p className="mb-2 last:mb-0">{children}</p>,
  ul: ({ children }: { children?: React.ReactNode }) => <ul className="mb-2 ml-4 list-disc">{children}</ul>,
  ol: ({ children }: { children?: React.ReactNode }) => <ol className="mb-2 ml-4 list-decimal">{children}</ol>,
  li: ({ children }: { children?: React.ReactNode }) => <li className="mb-1">{children}</li>,
  h1: ({ children }: { children?: React.ReactNode }) => <h1 className="text-lg font-bold mb-2">{children}</h1>,
  h2: ({ children }: { children?: React.ReactNode }) => <h2 className="text-base font-bold mb-2">{children}</h2>,
  h3: ({ children }: { children?: React.ReactNode }) => <h3 className="text-sm font-bold mb-1">{children}</h3>,
  h4: ({ children }: { children?: React.ReactNode }) => <h4 className="text-sm font-semibold mb-1">{children}</h4>,
  h5: ({ children }: { children?: React.ReactNode }) => <h5 className="text-xs font-semibold mb-1">{children}</h5>,
  h6: ({ children }: { children?: React.ReactNode }) => <h6 className="text-xs font-medium mb-1">{children}</h6>,
  table: ({ children }: { children?: React.ReactNode }) => (
    <div className="overflow-x-auto my-2">
      <table className="min-w-full border-collapse text-sm">{children}</table>
    </div>
  ),
  thead: ({ children }: { children?: React.ReactNode }) => (
    <thead className="bg-slate-100 dark:bg-slate-800">{children}</thead>
  ),
  th: ({ children }: { children?: React.ReactNode }) => (
    <th className="border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-left text-xs font-semibold">
      {children}
    </th>
  ),
  td: ({ children }: { children?: React.ReactNode }) => (
    <td className="border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-xs">
      {children}
    </td>
  ),
  }
}

/**
 * Shared markdown renderer for all message types.
 *
 * Uses Streamdown for both streaming and static rendering:
 *  - streaming: handles incomplete/unterminated markdown, shows a caret
 *  - static (mode="static"): same output, zero animation overhead
 *
 * @param content        - The raw text / markdown to render.
 * @param isStreaming    - Pass true while tokens are still arriving.
 * @param autoFormatJson - When true (default), raw JSON strings are wrapped in
 *                         a fenced code block for pretty-printing.
 */
export function MarkdownContent({
  content,
  isStreaming = false,
  autoFormatJson = true,
  className,
}: {
  content: string
  isStreaming?: boolean
  autoFormatJson?: boolean
  className?: string
}) {
  const formatted = autoFormatJson ? formatIfJson(content) : content
  const processedContent = processMentions(formatted)
  // Memoize components by isStreaming to avoid Streamdown re-rendering on every render
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const components = React.useMemo(() => makeComponents(isStreaming), [isStreaming])

  return (
    <div className={cn("min-w-0 text-sm leading-relaxed text-inherit", className)}>
      <Streamdown
        mode={isStreaming ? 'streaming' : 'static'}
        caret={isStreaming ? 'block' : undefined}
        components={components}
        rehypePlugins={[rehypeHighlight]}
      >
        {processedContent}
      </Streamdown>
    </div>
  )
}

/**
 * Lightweight renderer for user messages.
 *
 * Renders plain text with:
 * - @mentions as clickable links
 * - Bare URLs auto-linked and opened in new tabs
 *
 * Does NOT render full markdown (no headings, bold, images, etc.) to avoid
 * surprising formatting of user input and to keep the user bubble styling clean.
 */
export function LinkifiedContent({ content }: { content: string }) {
  const parts: (string | React.JSX.Element)[] = []
  let lastIndex = 0
  let keyIndex = 0

  const pushTextWithLineBreaks = (text: string) => {
    const lines = text.split('\n')
    lines.forEach((line, i) => {
      if (i > 0) parts.push(<br key={`br-${keyIndex++}`} />)
      if (line) parts.push(line)
    })
  }

  const combinedRegex = /<@([^|]+)\|([^>]+)>|(https?:\/\/[^\s<>)"'\]]+)/g
  let match

  while ((match = combinedRegex.exec(content)) !== null) {
    if (match.index > lastIndex) {
      pushTextWithLineBreaks(content.slice(lastIndex, match.index))
    }

    if (match[1] && match[2]) {
      const agentId = match[1]
      const agentName = match[2]
      parts.push(
        <a
          key={`link-${keyIndex++}`}
          href={`/c/agents/${agentId}`}
          target="_blank"
          rel="noopener noreferrer"
          className="room-mention mx-1 hover:underline underline-offset-2 transition-opacity hover:opacity-80"
          title={`Agent: ${agentName}`}
        >
          @{agentName}
        </a>
      )
    } else if (match[3]) {
      const url = match[3]
      parts.push(
        <a
          key={`link-${keyIndex++}`}
          href={url}
          target="_blank"
          rel="noopener noreferrer"
          className="underline underline-offset-2 hover:opacity-80 transition-opacity"
        >
          {url}
        </a>
      )
    }

    lastIndex = match.index + match[0].length
  }

  if (lastIndex < content.length) {
    pushTextWithLineBreaks(content.slice(lastIndex))
  }

  return <>{parts.length > 0 ? parts : content}</>
}
