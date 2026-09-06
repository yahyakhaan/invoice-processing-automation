export type RunStatus = "CREATED" | "RUNNING" | "PENDING_REVIEW" | "COMPLETED" | "FAILED";

export interface HealthResponse {
  status: "ok";
  database_backend: "postgres" | "sqlite";
  provider: string;
}

export interface SampleInvoice {
  id: string;
  name: string;
  format: string;
  size_bytes: number | null;
  is_demo: boolean;
  content_url: string | null;
}

export interface RunCreated {
  run_id: string;
  thread_id: string;
  status: RunStatus;
  detail_url: string;
  events_url: string;
  stream_url: string;
  review_url: string;
}

export interface RunSummary {
  run_id: string;
  thread_id: string;
  status: RunStatus;
  outcome: string | null;
  invoice_id: string | null;
  source: string | null;
  provider: string;
  model: string;
  created_at: string;
  updated_at: string;
}

export interface ValidationFlag {
  code: string;
  severity: "blocking" | "warning" | "info";
  message: string;
}

export interface RunDetail extends RunSummary {
  invoice: Record<string, unknown> | null;
  validation: {
    flags?: ValidationFlag[];
    usd_equivalent?: number | null;
    summary?: string;
  } | null;
  approval_draft: Record<string, unknown> | null;
  approval_critique: Record<string, unknown> | null;
  approval_final: Record<string, unknown> | null;
  human_review: Record<string, unknown> | null;
  payment: Record<string, unknown> | null;
  pending_interrupt: Record<string, unknown> | null;
  agentic_mode: boolean;
  model_calls: number;
  tool_calls: number;
  error: string | null;
}

export interface PublicEvent {
  event_id: string;
  run_id: string;
  thread_id: string;
  sequence: number;
  timestamp: string;
  stage: string | null;
  event_type: string;
  status: RunStatus;
  level: string;
  message: string;
  data: Record<string, unknown>;
}

export interface ReviewInput {
  decision: "approve" | "reject";
  actor: string;
  rationale: string;
}
