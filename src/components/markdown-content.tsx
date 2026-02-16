'use client'

import React, { useCallback, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import rehypeHighlight from 'rehype-highlight'
import { Check, Copy } from 'lucide-react'
import { formatIfJson } from '@/lib/utils'

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
 * Convert <@agent_id|agent_name> mention syntax to markdown links.
 * Uses standard markdown link syntax so react-markdown handles them natively
 * without needing rehype-raw.
 */
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

/**
 * Shared markdown renderer for all message types.
 *
 * Features:
 * - Full GFM markdown with syntax highlighting
 * - @mention syntax rendered as clickable agent profile links
 * - All links open in a new tab (`target="_blank"`)
 * - Optional JSON auto-formatting (wraps raw JSON in a code block)
 *
 * @param content   - The raw text / markdown to render.
 * @param autoFormatJson - When true (default), raw JSON strings are wrapped in
 *                         a fenced code block for pretty-printing. Pass false
 *                         for user-authored messages where auto-formatting may
 *                         be surprising.
 */
export function MarkdownContent({
  content,
  autoFormatJson = true,
}: {
  content: string
  autoFormatJson?: boolean
}) {
  const formatted = autoFormatJson ? formatIfJson(content) : content
  const processedContent = processMentions(formatted)

  return (
    <div className="min-w-0 text-sm leading-relaxed text-inherit">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlight]}
        components={{
          // --- Links: always open in new tab ---
          a: ({ children, href, ...props }) => {
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
          // --- Code blocks ---
          code: ({ className, children, ...props }) => {
            const match = /language-(\w+)/.exec(className || '')
            const isInline = !match
            return isInline ? (
              <code
                className="bg-slate-100 dark:bg-slate-800 text-slate-700 dark:text-slate-200 px-1.5 py-0.5 rounded text-sm font-mono"
                {...props}
              >
                {children}
              </code>
            ) : (
              <CodeBlockWithCopy className={className} {...props}>
                {children}
              </CodeBlockWithCopy>
            )
          },
          // --- Block elements ---
          p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
          ul: ({ children }) => <ul className="mb-2 ml-4 list-disc">{children}</ul>,
          ol: ({ children }) => <ol className="mb-2 ml-4 list-decimal">{children}</ol>,
          li: ({ children }) => <li className="mb-1">{children}</li>,
          h1: ({ children }) => <h1 className="text-lg font-bold mb-2">{children}</h1>,
          h2: ({ children }) => <h2 className="text-base font-bold mb-2">{children}</h2>,
          h3: ({ children }) => <h3 className="text-sm font-bold mb-1">{children}</h3>,
          h4: ({ children }) => <h4 className="text-sm font-semibold mb-1">{children}</h4>,
          h5: ({ children }) => <h5 className="text-xs font-semibold mb-1">{children}</h5>,
          h6: ({ children }) => <h6 className="text-xs font-medium mb-1">{children}</h6>,
          // --- Tables: horizontally scrollable within the bubble ---
          table: ({ children }) => (
            <div className="overflow-x-auto my-2">
              <table className="min-w-full border-collapse text-sm">
                {children}
              </table>
            </div>
          ),
          thead: ({ children }) => (
            <thead className="bg-slate-100 dark:bg-slate-800">{children}</thead>
          ),
          th: ({ children }) => (
            <th className="border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-left text-xs font-semibold">
              {children}
            </th>
          ),
          td: ({ children }) => (
            <td className="border border-slate-200 dark:border-slate-700 px-3 py-1.5 text-xs">
              {children}
            </td>
          ),
        }}
      >
        {processedContent}
      </ReactMarkdown>
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

  // Combined regex: match @mentions OR bare URLs
  const combinedRegex = /<@([^|]+)\|([^>]+)>|(https?:\/\/[^\s<>)"'\]]+)/g
  let match

  while ((match = combinedRegex.exec(content)) !== null) {
    // Add text before the match
    if (match.index > lastIndex) {
      parts.push(content.slice(lastIndex, match.index))
    }

    if (match[1] && match[2]) {
      // @mention match
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
      // URL match
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

  // Add remaining text
  if (lastIndex < content.length) {
    parts.push(content.slice(lastIndex))
  }

  return <>{parts.length > 0 ? parts : content}</>
}
