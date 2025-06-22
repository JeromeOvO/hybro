/* tslint:disable */
/* eslint-disable */
/**
/* This file was automatically generated from pydantic models by running pydantic2ts.
/* Do not modify it by hand - just update the pydantic models and then re-run the script
*/

export interface Step {
  step_id: string;
  description: string;
  agent_id?: string | null;
  status?: string;
  input_data?: unknown;
  output_data?: unknown;
  priority?: number;
  dependencies?: string[];
  error?: string | null;
  result?: unknown;
  agent_name?: string | null;
  is_remote_agent?: boolean | null;
}
export interface TaskResponse {
  task_id: string;
  status?: string;
  steps?: Step[];
  result?: unknown;
  error?: string | null;
}
export interface UserResponse {
  session_id: string;
  task_id: string;
  result: string;
}
