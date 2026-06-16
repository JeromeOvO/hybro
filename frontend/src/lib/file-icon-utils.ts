import {
  FileSpreadsheet,
  FileText,
  FileCode,
  FileArchive,
  FileIcon,
  Presentation,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'

interface FileIconConfig {
  icon: LucideIcon
  color: string
}

const MIME_FILE_ICONS: [RegExp, FileIconConfig][] = [
  // Spreadsheets — green
  [/spreadsheetml|\.csv$|^text\/csv$/, { icon: FileSpreadsheet, color: 'text-green-600' }],
  // Word docs — blue
  [/wordprocessingml|msword/, { icon: FileText, color: 'text-blue-600' }],
  // Presentations — orange
  [/presentationml|powerpoint/, { icon: Presentation, color: 'text-orange-500' }],
  // PDF — red
  [/^application\/pdf$/, { icon: FileText, color: 'text-red-500' }],
  // Archives — amber
  [/^application\/(zip|x-tar|gzip|x-7z|x-rar)/, { icon: FileArchive, color: 'text-amber-600' }],
  // JSON / XML / code — purple
  [/^application\/(json|xml)$/, { icon: FileCode, color: 'text-purple-500' }],
  // Plain text / markdown / HTML — slate
  [/^text\//, { icon: FileText, color: 'text-slate-500' }],
]

const EXT_FILE_ICONS: [RegExp, FileIconConfig][] = [
  [/\.(xlsx?|csv)$/i, { icon: FileSpreadsheet, color: 'text-green-600' }],
  [/\.(docx?)$/i, { icon: FileText, color: 'text-blue-600' }],
  [/\.(pptx?)$/i, { icon: Presentation, color: 'text-orange-500' }],
  [/\.pdf$/i, { icon: FileText, color: 'text-red-500' }],
  [/\.(zip|tar|gz|7z|rar)$/i, { icon: FileArchive, color: 'text-amber-600' }],
  [/\.(json|xml|ya?ml|toml)$/i, { icon: FileCode, color: 'text-purple-500' }],
  [/\.(txt|md|html?|csv)$/i, { icon: FileText, color: 'text-slate-500' }],
]

const DEFAULT_FILE_ICON: FileIconConfig = { icon: FileIcon, color: 'text-muted-foreground' }

/** Get a file-type-specific icon and color from MIME type and/or filename. */
export function getFileIcon(mimeType?: string, fileName?: string): FileIconConfig {
  if (mimeType) {
    for (const [pattern, config] of MIME_FILE_ICONS) {
      if (pattern.test(mimeType)) return config
    }
  }
  if (fileName) {
    for (const [pattern, config] of EXT_FILE_ICONS) {
      if (pattern.test(fileName)) return config
    }
  }
  return DEFAULT_FILE_ICON
}
