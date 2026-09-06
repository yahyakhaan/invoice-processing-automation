import type { ReviewInput, RunCreated, RunDetail } from "./types";

interface ModelContextTool {
  name: string;
  title: string;
  description: string;
  inputSchema: Record<string, unknown>;
  annotations: { readOnlyHint: boolean; untrustedContentHint: boolean };
  execute(input: unknown): unknown | Promise<unknown>;
}

declare global {
  interface Document {
    readonly modelContext?: {
      registerTool(tool: ModelContextTool, options?: { signal?: AbortSignal }): void | Promise<void>;
    };
  }
}

interface WebMcpActions {
  startSample(sampleId: string): Promise<RunCreated>;
  getRun(runId: string): Promise<RunDetail>;
  reviewRun(runId: string, input: ReviewInput): Promise<{ run_id: string; status: string }>;
  reportError(error: unknown): void;
}

function objectInput(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("Tool input must be an object");
  }
  return value as Record<string, unknown>;
}

function requiredString(input: Record<string, unknown>, key: string): string {
  const value = input[key];
  if (typeof value !== "string" || !value.trim()) throw new Error(`${key} is required`);
  return value.trim();
}

export function registerWebMcpTools(actions: WebMcpActions): () => void {
  const context = document.modelContext;
  if (!context?.registerTool) return () => undefined;
  const lifecycle = new AbortController();
  const register = (tool: ModelContextTool) => {
    try {
      void Promise.resolve(context.registerTool(tool, { signal: lifecycle.signal })).catch(
        actions.reportError,
      );
    } catch (error) {
      actions.reportError(error);
    }
  };

  register({
    name: "start_invoice_sample_run",
    title: "Start sample invoice run",
    description: "Start processing one bundled sample invoice and select the new run in the console.",
    inputSchema: {
      type: "object",
      properties: { sampleId: { type: "string" } },
      required: ["sampleId"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, untrustedContentHint: false },
    async execute(value) {
      const input = objectInput(value);
      const created = await actions.startSample(requiredString(input, "sampleId"));
      return { runId: created.run_id, status: created.status };
    },
  });

  register({
    name: "read_invoice_run",
    title: "Read invoice run",
    description: "Read the current status and result for an invoice run without changing it.",
    inputSchema: {
      type: "object",
      properties: { runId: { type: "string", format: "uuid" } },
      required: ["runId"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true, untrustedContentHint: true },
    async execute(value) {
      const run = await actions.getRun(requiredString(objectInput(value), "runId"));
      return { runId: run.run_id, status: run.status, outcome: run.outcome };
    },
  });

  register({
    name: "review_invoice_run",
    title: "Review pending invoice",
    description: "Approve or reject an invoice run that is currently waiting for VP review.",
    inputSchema: {
      type: "object",
      properties: {
        runId: { type: "string", format: "uuid" },
        decision: { type: "string", enum: ["approve", "reject"] },
        actor: { type: "string" },
        rationale: { type: "string" },
      },
      required: ["runId", "decision", "actor", "rationale"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false, untrustedContentHint: false },
    async execute(value) {
      const input = objectInput(value);
      const decision = requiredString(input, "decision");
      if (decision !== "approve" && decision !== "reject") {
        throw new Error("decision must be approve or reject");
      }
      return actions.reviewRun(requiredString(input, "runId"), {
        decision,
        actor: requiredString(input, "actor"),
        rationale: requiredString(input, "rationale"),
      });
    },
  });

  return () => lifecycle.abort();
}
