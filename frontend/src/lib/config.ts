import { parsePublicConfig } from './config-schema'

// Next.js inlines only this public JSON projection at build time.
export const config = parsePublicConfig(process.env.HYBRO_FRONTEND_CONFIG)
