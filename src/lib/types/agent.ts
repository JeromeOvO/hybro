/**
 * Agent-related types.
 *
 * A2A protocol types (AgentCard, AgentSkill, security schemes, etc.) are
 * imported from the canonical @a2a-js/sdk package.  Only backend-specific
 * agent wrapper types are defined here.
 */

// ── Re-export A2A protocol types from SDK ────────────────────────────────
export type {
  AgentCard,
  AgentCapabilities,
  AgentExtension,
  AgentProvider,
  AgentSkill,
  SecurityScheme,
  APIKeySecurityScheme,
  HTTPAuthSecurityScheme,
  OAuth2SecurityScheme,
  OpenIdConnectSecurityScheme,
  OAuthFlows,
  AuthorizationCodeOAuthFlow,
  ClientCredentialsOAuthFlow,
  ImplicitOAuthFlow,
  PasswordOAuthFlow,
} from '@a2a-js/sdk'

import type { AgentCard } from '@a2a-js/sdk'

// ── Types NOT in SDK ─────────────────────────────────────────────────────

/**
 * The location of the API key.
 * (Not exported from @a2a-js/sdk as a standalone type.)
 */
export type In = "cookie" | "header" | "query";

export type AgentStatus = "active" | "inactive" | "deleted";

export interface Agent {
  agent_id: string;
  agent_card: AgentCard;
  agent_status?: AgentStatus;
  call_count?: number;
  call_success_count?: number;
  like_count?: number;
  dislike_count?: number;
}
