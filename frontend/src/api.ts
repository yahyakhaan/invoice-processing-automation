import type {
  HealthResponse,
  PublicEvent,
  ReviewInput,
  RunCreated,
  RunDetail,
  RunSummary,
  SampleInvoice,
} from "./types";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // The generic status message remains useful for non-JSON failures.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<HealthResponse>("/api/health"),
  samples: () => request<SampleInvoice[]>("/api/samples"),
  runs: () => request<{ items: RunSummary[] }>("/api/runs"),
  run: (runId: string) => request<RunDetail>(`/api/runs/${runId}`),
  events: (runId: string, after = 0) =>
    request<{ items: PublicEvent[] }>(`/api/runs/${runId}/events?after=${after}`),
  createRun: (source: { file?: File; sampleId?: string }) => {
    const body = new FormData();
    if (source.file) body.append("file", source.file);
    if (source.sampleId) body.append("sample_id", source.sampleId);
    return request<RunCreated>("/api/runs", { method: "POST", body });
  },
  review: (runId: string, input: ReviewInput) =>
    request<{ run_id: string; status: "RUNNING"; stream_url: string }>(
      `/api/runs/${runId}/review`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(input),
      },
    ),
};
