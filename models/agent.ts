/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

export interface Agent {
  agent_id: string;
  agentCard: AgentCard;
  is_remote?: boolean;
  ragUrl?: string | null;
}
export interface AgentCard {
  name: string;
  description?: string | null;
  url: string;
  provider?: AgentProvider | null;
  version: string;
  documentationUrl?: string | null;
  capabilities: AgentCapabilities;
  authentication?: AgentAuthentication | null;
  defaultInputModes?: string[];
  defaultOutputModes?: string[];
  skills: AgentSkill[];
}
export interface AgentProvider {
  organization: string;
  url?: string | null;
  [k: string]: unknown;
}
export interface AgentCapabilities {
  streaming?: boolean;
  pushNotifications?: boolean;
  stateTransitionHistory?: boolean;
  [k: string]: unknown;
}
export interface AgentAuthentication {
  schemes: string[];
  credentials?: string | null;
  [k: string]: unknown;
}
export interface AgentSkill {
  id: string;
  name: string;
  description?: string | null;
  tags?: string[] | null;
  examples?: string[] | null;
  inputModes?: string[] | null;
  outputModes?: string[] | null;
  [k: string]: unknown;
}
