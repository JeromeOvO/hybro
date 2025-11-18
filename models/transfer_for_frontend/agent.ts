/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

export type SecurityScheme =
  | APIKeySecurityScheme
  | HTTPAuthSecurityScheme
  | OAuth2SecurityScheme
  | OpenIdConnectSecurityScheme;
/**
 * The location of the API key. Valid values are "query", "header", or "cookie".
 */
export type In = "cookie" | "header" | "query";
export type AgentStatus = "active" | "inactive" | "deleted";

export interface Agent {
  agent_id: string;
  provider_id?: string | null;
  agent_card: AgentCard;
  agent_status?: AgentStatus;
  call_count?: number;
  call_success_count?: number;
  like_count?: number;
  dislike_count?: number;
}
/**
 * An AgentCard conveys key information:
 * - Overall details (version, name, description, uses)
 * - Skills: A set of capabilities the agent can perform
 * - Default modalities/content types supported by the agent.
 * - Authentication requirements
 */
export interface AgentCard {
  capabilities: AgentCapabilities;
  defaultInputModes: string[];
  defaultOutputModes: string[];
  description: string;
  documentationUrl?: string | null;
  iconUrl?: string | null;
  name: string;
  provider?: AgentProvider | null;
  security?:
    | {
        [k: string]: string[];
      }[]
    | null;
  securitySchemes?: {
    [k: string]: SecurityScheme;
  } | null;
  skills: AgentSkill[];
  supportsAuthenticatedExtendedCard?: boolean | null;
  url: string;
  version: string;
}
/**
 * Defines optional capabilities supported by an agent.
 */
export interface AgentCapabilities {
  extensions?: AgentExtension[] | null;
  pushNotifications?: boolean | null;
  stateTransitionHistory?: boolean | null;
  streaming?: boolean | null;
  [k: string]: unknown;
}
/**
 * A declaration of an extension supported by an Agent.
 */
export interface AgentExtension {
  description?: string | null;
  params?: {
    [k: string]: unknown;
  } | null;
  required?: boolean | null;
  uri: string;
  [k: string]: unknown;
}
/**
 * Represents the service provider of an agent.
 */
export interface AgentProvider {
  organization: string;
  url: string;
  [k: string]: unknown;
}
/**
 * API Key security scheme.
 */
export interface APIKeySecurityScheme {
  description?: string | null;
  in: In;
  name: string;
  type?: "apiKey";
  [k: string]: unknown;
}
/**
 * HTTP Authentication security scheme.
 */
export interface HTTPAuthSecurityScheme {
  bearerFormat?: string | null;
  description?: string | null;
  scheme: string;
  type?: "http";
  [k: string]: unknown;
}
/**
 * OAuth2.0 security scheme configuration.
 */
export interface OAuth2SecurityScheme {
  description?: string | null;
  flows: OAuthFlows;
  type?: "oauth2";
  [k: string]: unknown;
}
/**
 * Allows configuration of the supported OAuth Flows
 */
export interface OAuthFlows {
  authorizationCode?: AuthorizationCodeOAuthFlow | null;
  clientCredentials?: ClientCredentialsOAuthFlow | null;
  implicit?: ImplicitOAuthFlow | null;
  password?: PasswordOAuthFlow | null;
  [k: string]: unknown;
}
/**
 * Configuration details for a supported OAuth Flow
 */
export interface AuthorizationCodeOAuthFlow {
  authorizationUrl: string;
  refreshUrl?: string | null;
  scopes: {
    [k: string]: string;
  };
  tokenUrl: string;
  [k: string]: unknown;
}
/**
 * Configuration details for a supported OAuth Flow
 */
export interface ClientCredentialsOAuthFlow {
  refreshUrl?: string | null;
  scopes: {
    [k: string]: string;
  };
  tokenUrl: string;
  [k: string]: unknown;
}
/**
 * Configuration details for a supported OAuth Flow
 */
export interface ImplicitOAuthFlow {
  authorizationUrl: string;
  refreshUrl?: string | null;
  scopes: {
    [k: string]: string;
  };
  [k: string]: unknown;
}
/**
 * Configuration details for a supported OAuth Flow
 */
export interface PasswordOAuthFlow {
  refreshUrl?: string | null;
  scopes: {
    [k: string]: string;
  };
  tokenUrl: string;
  [k: string]: unknown;
}
/**
 * OpenID Connect security scheme configuration.
 */
export interface OpenIdConnectSecurityScheme {
  description?: string | null;
  openIdConnectUrl: string;
  type?: "openIdConnect";
  [k: string]: unknown;
}
/**
 * Represents a unit of capability that an agent can perform.
 */
export interface AgentSkill {
  description: string;
  examples?: string[] | null;
  id: string;
  inputModes?: string[] | null;
  name: string;
  outputModes?: string[] | null;
  tags: string[];
  [k: string]: unknown;
}
