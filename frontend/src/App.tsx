import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpRight,
  Check,
  CheckCircle2,
  ChevronDown,
  Circle,
  Clock3,
  FileCheck2,
  FileText,
  FileUp,
  History,
  LoaderCircle,
  RefreshCw,
  RotateCcw,
  ShieldCheck,
  X,
  XCircle,
} from "lucide-react";
import {
  type ChangeEvent,
  type DragEvent,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { api } from "./api";
import type {
  HealthResponse,
  PublicEvent,
  ReviewInput,
  RunCreated,
  RunDetail,
  RunStatus,
  RunSummary,
  SampleInvoice,
  ValidationFlag,
} from "./types";
import { registerWebMcpTools } from "./webmcp";

const STAGES = [
  "INGEST",
  "NORMALIZE",
  "VALIDATE",
  "APPROVE",
  "CRITIQUE",
  "GATE",
  "HUMAN_REVIEW",
  "PAY",
  "REPORT",
] as const;

const CLOSED_STATUSES: RunStatus[] = ["PENDING_REVIEW", "COMPLETED", "FAILED"];

function displayStage(stage: string) {
  return stage === "HUMAN_REVIEW" ? "Review" : stage.charAt(0) + stage.slice(1).toLowerCase();
}

function normalizedStage(event: PublicEvent): string | null {
  if (event.stage === "PAYMENT") return "GATE";
  return event.stage;
}

function statusLabel(status: RunStatus) {
  const labels: Record<RunStatus, string> = {
    CREATED: "Queued",
    RUNNING: "Processing",
    PENDING_REVIEW: "Needs review",
    COMPLETED: "Complete",
    FAILED: "Failed",
  };
  return labels[status];
}

function statusTone(status: RunStatus) {
  if (status === "COMPLETED") return "success";
  if (status === "FAILED") return "danger";
  if (status === "PENDING_REVIEW") return "warning";
  if (status === "RUNNING") return "active";
  return "neutral";
}

function formatMoney(value: unknown, currency = "USD") {
  if (typeof value !== "number") return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(value);
}

function formatTime(value: string) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date);
}

function shortSource(source: string | null) {
  return (source || "Unknown source").replace(/^sample:|^upload:/, "");
}

function eventDetail(event: PublicEvent) {
  const detail = event.data.detail;
  if (typeof detail === "string") return detail;
  const outcome = event.data.outcome;
  if (typeof outcome === "string") return `Outcome: ${outcome}`;
  const flags = event.data.flags;
  if (Array.isArray(flags) && flags.length) return flags.join(", ");
  return null;
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [samples, setSamples] = useState<SampleInvoice[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [selectedSample, setSelectedSample] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [selectedRun, setSelectedRun] = useState<RunDetail | null>(null);
  const [events, setEvents] = useState<PublicEvent[]>([]);
  const [loadingRun, setLoadingRun] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actor, setActor] = useState("VP");
  const [rationale, setRationale] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const streamRef = useRef<EventSource | null>(null);
  const eventsRef = useRef<PublicEvent[]>([]);

  useEffect(() => {
    eventsRef.current = events;
  }, [events]);

  const refreshRuns = useCallback(async () => {
    const response = await api.runs();
    setRuns(response.items);
    return response.items;
  }, []);

  const refreshSelectedRun = useCallback(async (runId: string) => {
    const detail = await api.run(runId);
    setSelectedRun(detail);
    return detail;
  }, []);

  const closeStream = useCallback(() => {
    streamRef.current?.close();
    streamRef.current = null;
  }, []);

  const connectStream = useCallback(
    (runId: string, after = 0) => {
      closeStream();
      const stream = new EventSource(`/api/runs/${runId}/events/stream?after=${after}`);
      streamRef.current = stream;
      stream.addEventListener("run-event", (message) => {
        let next: PublicEvent;
        try {
          next = JSON.parse((message as MessageEvent<string>).data) as PublicEvent;
        } catch {
          setError("A live event could not be read. Run history is still available.");
          return;
        }
        setEvents((current) => {
          if (current.some((item) => item.sequence === next.sequence)) return current;
          return [...current, next].sort((a, b) => a.sequence - b.sequence);
        });
        if (CLOSED_STATUSES.includes(next.status)) {
          stream.close();
          if (streamRef.current === stream) streamRef.current = null;
          void Promise.all([refreshSelectedRun(runId), refreshRuns()]).catch((cause: unknown) => {
            setError(cause instanceof Error ? cause.message : "Could not refresh the finished run");
          });
        }
      });
      stream.onerror = () => {
        stream.close();
        if (streamRef.current === stream) streamRef.current = null;
        void refreshSelectedRun(runId).catch(() => undefined);
      };
    },
    [closeStream, refreshRuns, refreshSelectedRun],
  );

  const selectRun = useCallback(
    async (runId: string) => {
      setLoadingRun(true);
      setError(null);
      closeStream();
      try {
        const [detail, replay] = await Promise.all([api.run(runId), api.events(runId)]);
        setSelectedRun(detail);
        setEvents(replay.items);
        if (detail.status === "RUNNING" || detail.status === "CREATED") {
          connectStream(runId, replay.items.at(-1)?.sequence || 0);
        }
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "Could not load run");
      } finally {
        setLoadingRun(false);
      }
    },
    [closeStream, connectStream],
  );

  const bootstrap = useCallback(async () => {
    setError(null);
    try {
      const [healthResponse, sampleResponse, runResponse] = await Promise.all([
        api.health(),
        api.samples(),
        api.runs(),
      ]);
      setHealth(healthResponse);
      setSamples(sampleResponse);
      setRuns(runResponse.items);
      const first = runResponse.items[0];
      if (first) void selectRun(first.run_id);
    } catch (cause) {
      setHealth(null);
      setError(cause instanceof Error ? cause.message : "Could not connect to the invoice API");
    }
  }, [selectRun]);

  useEffect(() => {
    void bootstrap();
    return closeStream;
  }, [bootstrap, closeStream]);

  const startSource = useCallback(
    async (source: { file?: File; sampleId?: string }): Promise<RunCreated> => {
      setSubmitting(true);
      setError(null);
      setNotice(null);
      closeStream();
      try {
        const created = await api.createRun(source);
        setEvents([]);
        const [detail, currentHealth] = await Promise.all([
          refreshSelectedRun(created.run_id),
          api.health(),
        ]);
        setHealth(currentHealth);
        setRuns((current) => [detail, ...current.filter((item) => item.run_id !== detail.run_id)]);
        connectStream(created.run_id);
        setSelectedFile(null);
        setSelectedSample("");
        setNotice(
          detail.agentic_mode
            ? "LLM-powered run started. Live workflow events are connected."
            : "Deterministic demo run started. Live workflow events are connected.",
        );
        return created;
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : "Could not start run";
        setError(message);
        throw cause;
      } finally {
        setSubmitting(false);
      }
    },
    [closeStream, connectStream, refreshSelectedRun],
  );

  const startRun = async () => {
    try {
      if (selectedFile) await startSource({ file: selectedFile });
      else if (selectedSample) await startSource({ sampleId: selectedSample });
    } catch {
      // startSource already renders the user-facing error.
    }
  };

  const submitReview = useCallback(
    async (runId: string, input: ReviewInput) => {
      setSubmitting(true);
      setError(null);
      try {
        const accepted = await api.review(runId, input);
        setSelectedRun((current) => (current ? { ...current, status: "RUNNING" } : current));
        setNotice(`${input.decision === "approve" ? "Approval" : "Rejection"} submitted.`);
        connectStream(runId, eventsRef.current.at(-1)?.sequence || 0);
        return accepted;
      } catch (cause) {
        const message = cause instanceof Error ? cause.message : "Could not submit review";
        setError(message);
        throw cause;
      } finally {
        setSubmitting(false);
      }
    },
    [connectStream],
  );

  const review = async (decision: "approve" | "reject") => {
    if (!selectedRun) return;
    try {
      await submitReview(selectedRun.run_id, { decision, actor, rationale });
    } catch {
      // submitReview already renders the user-facing error.
    }
  };

  useEffect(
    () =>
      registerWebMcpTools({
        startSample: (sampleId) => startSource({ sampleId }),
        getRun: async (runId) => {
          const detail = await refreshSelectedRun(runId);
          await selectRun(runId);
          return detail;
        },
        reviewRun: submitReview,
        reportError: (cause) => {
          console.warn("WebMCP tool registration failed", cause);
        },
      }),
    [refreshSelectedRun, selectRun, startSource, submitReview],
  );

  const chooseFile = (file: File | undefined) => {
    if (!file) return;
    setSelectedFile(file);
    setSelectedSample("");
    setError(null);
  };

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    chooseFile(event.target.files?.[0]);
    event.target.value = "";
  };

  const onDrop = (event: DragEvent<HTMLButtonElement>) => {
    event.preventDefault();
    setDragging(false);
    chooseFile(event.dataTransfer.files[0]);
  };

  const currentStage = useMemo(
    () =>
      [...events]
        .reverse()
        .map(normalizedStage)
        .find((stage) => stage && STAGES.includes(stage as (typeof STAGES)[number])),
    [events],
  );
  const reachedStages = useMemo(
    () => new Set(events.map(normalizedStage).filter((stage): stage is string => Boolean(stage))),
    [events],
  );
  const flags = (selectedRun?.validation?.flags || []) as ValidationFlag[];
  const invoice = selectedRun?.invoice || {};
  const currency = typeof invoice.currency === "string" ? invoice.currency : "USD";
  const finalDecision = selectedRun?.approval_final?.decision;
  const humanDecision = selectedRun?.human_review?.decision;

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Acme AP Control home">
          <span className="brand-mark">A</span>
          <span><strong>ACME</strong><small>AP CONTROL</small></span>
        </a>
        <div className="topbar-status">
          <span className={`live-dot ${health ? "online" : ""}`} />
          {health ? `${health.database_backend} · ${health.provider}` : "Connecting"}
        </div>
        <button className="icon-button" aria-label="Refresh workspace" onClick={() => void bootstrap()}>
          <RefreshCw size={18} />
        </button>
      </header>

      {(error || notice) && (
        <div className={`global-banner ${error ? "error" : "notice"}`} role={error ? "alert" : "status"}>
          {error ? <AlertTriangle size={17} /> : <CheckCircle2 size={17} />}
          <span>{error || notice}</span>
          {error && !health && <button className="banner-retry" onClick={() => void bootstrap()}><RefreshCw size={14} /> Retry</button>}
          <button aria-label="Dismiss message" onClick={() => { setError(null); setNotice(null); }}><X size={16} /></button>
        </div>
      )}

      <main className="workspace">
        <section className="intake-panel" aria-labelledby="new-run-title">
          <div className="eyebrow">NEW RUN</div>
          <h1 id="new-run-title">Process an invoice</h1>
          <p className="muted">Upload a non-sensitive source file or start with a bundled test invoice.</p>

          <input ref={fileInput} className="sr-only" type="file" accept=".pdf,.csv,.json,.xml,.txt" onChange={onFileChange} />
          {selectedFile ? (
            <div className="selected-source">
              <span className="upload-icon"><FileText size={21} /></span>
              <span><strong>{selectedFile.name}</strong><small>{(selectedFile.size / 1024).toFixed(1)} KB · Ready</small></span>
              <button aria-label="Remove selected file" onClick={() => setSelectedFile(null)}><X size={17} /></button>
            </div>
          ) : (
            <button
              className={`upload-zone ${dragging ? "dragging" : ""}`}
              type="button"
              onClick={() => fileInput.current?.click()}
              onDragEnter={(event) => { event.preventDefault(); setDragging(true); }}
              onDragOver={(event) => event.preventDefault()}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
            >
              <span className="upload-icon"><FileUp size={22} /></span>
              <span><strong>Drop invoice here</strong><small>PDF, CSV, JSON, XML or TXT · 10 MB max</small></span>
              <span className="browse-label">Browse</span>
            </button>
          )}

          <div className="divider"><span>OR USE A SAMPLE</span></div>
          <label className="sample-select">
            <span className="file-glyph"><FileCheck2 size={18} /></span>
            <span className="select-copy"><small>Sample invoice</small><strong>{selectedSample ? samples.find((sample) => sample.id === selectedSample)?.name : "Choose from the test corpus"}</strong></span>
            <ChevronDown size={18} />
            <select value={selectedSample} onChange={(event) => { setSelectedSample(event.target.value); setSelectedFile(null); }} aria-label="Choose a sample invoice">
              <option value="">Choose a sample</option>
              {samples.map((sample) => <option key={sample.id} value={sample.id}>{sample.name}</option>)}
            </select>
          </label>

          <button className="primary-button" type="button" disabled={submitting || (!selectedFile && !selectedSample)} onClick={() => void startRun()}>
            {submitting ? <><LoaderCircle className="spin" size={17} /> Starting…</> : <>Start processing <ArrowUpRight size={17} /></>}
          </button>

          <div className="control-note">
            <ShieldCheck size={17} />
            <span><strong>Payment controls stay enforced.</strong> High-value invoices pause for your approval.</span>
          </div>
          {health && (
            <div className="local-note">
              <Activity size={15} />
              {health.llm_enabled
                ? `${health.llm_runs_remaining} of ${health.llm_daily_limit} live LLM demo runs remain today. Later runs automatically use deterministic mode.`
                : "This deployment is using deterministic demo mode; no LLM key is required."}
            </div>
          )}
          {health?.database_backend === "sqlite" && (
            <div className="local-note"><RotateCcw size={15} /> Web history lasts for this server session. Use PostgreSQL for restart-safe history.</div>
          )}
        </section>

        <section className="monitor-panel" aria-labelledby="monitor-title">
          <div className="panel-heading">
            <div>
              <div className="eyebrow">WORKFLOW</div>
              <h2 id="monitor-title">{selectedRun?.invoice_id || (selectedRun ? shortSource(selectedRun.source) : "Run monitor")}</h2>
              {selectedRun && <div className="run-id">{selectedRun.run_id}</div>}
            </div>
            <div className="heading-actions">
              {selectedRun && ["COMPLETED", "FAILED"].includes(selectedRun.status) && (
                <a className="download-button" href={`/api/runs/${selectedRun.run_id}/artifact`} download><ArrowDownToLine size={15} /> Audit</a>
              )}
              <span className={`status-pill ${selectedRun ? statusTone(selectedRun.status) : "neutral"}`}>
                {selectedRun?.status === "RUNNING" ? <LoaderCircle className="spin" size={14} /> : <Clock3 size={14} />}
                {selectedRun ? statusLabel(selectedRun.status) : "Waiting"}
              </span>
            </div>
          </div>

          <ol className="stage-rail" aria-label="Invoice processing stages">
            {STAGES.map((stage, index) => {
              const isActive = selectedRun?.status === "RUNNING" && currentStage === stage;
              const isDone = reachedStages.has(stage) && !isActive;
              const isSkipped = stage === "HUMAN_REVIEW" && selectedRun && selectedRun.status !== "PENDING_REVIEW" && selectedRun.outcome;
              return (
                <li key={stage} className={`${isActive ? "active" : ""} ${isDone ? "done" : ""} ${isSkipped ? "skipped" : ""}`}>
                  <span className="stage-marker">{isDone ? <Check size={12} /> : String(index + 1).padStart(2, "0")}</span>
                  <span>{displayStage(stage)}</span>
                </li>
              );
            })}
          </ol>

          {loadingRun ? (
            <div className="empty-monitor"><LoaderCircle className="spin" size={28} /><p>Loading run details…</p></div>
          ) : !selectedRun ? (
            <div className="empty-monitor">
              <div className="pulse-ring"><Activity size={25} /></div>
              <h3>No active run</h3>
              <p>Select an invoice to watch every validation, decision, and handoff as it happens.</p>
            </div>
          ) : (
            <div className="run-content">
              {selectedRun.status === "PENDING_REVIEW" && (
                <section className="review-card" aria-labelledby="review-title">
                  <div className="review-heading"><span><ShieldCheck size={20} /></span><div><div className="eyebrow">ACTION REQUIRED</div><h3 id="review-title">VP approval needed</h3></div></div>
                  <p>This invoice cleared deterministic controls but exceeds the automatic payment threshold.</p>
                  <div className="review-amount">{formatMoney(selectedRun.validation?.usd_equivalent, "USD")}<small>USD equivalent</small></div>
                  <div className="review-fields">
                    <label>Reviewer<input value={actor} maxLength={120} onChange={(event) => setActor(event.target.value)} /></label>
                    <label>Rationale<textarea value={rationale} maxLength={2000} rows={2} placeholder="Add a short decision note" onChange={(event) => setRationale(event.target.value)} /></label>
                  </div>
                  <div className="review-actions">
                    <button className="reject-button" disabled={submitting || !actor.trim()} onClick={() => void review("reject")}><XCircle size={17} /> Reject</button>
                    <button className="approve-button" disabled={submitting || !actor.trim()} onClick={() => void review("approve")}><CheckCircle2 size={17} /> Approve & resume</button>
                  </div>
                </section>
              )}

              <div className="summary-grid">
                <div><small>Vendor</small><strong>{String(invoice.vendor_canonical || invoice.vendor_raw || "—")}</strong></div>
                <div><small>Invoice total</small><strong>{formatMoney(invoice.total, currency)}</strong></div>
                <div><small>Decision</small><strong>{typeof humanDecision === "string" ? humanDecision : selectedRun.outcome || (typeof finalDecision === "string" ? finalDecision : "Pending")}</strong></div>
                <div><small>Execution</small><strong>{selectedRun.agentic_mode ? `${selectedRun.model_calls} model calls` : "Deterministic"}</strong></div>
              </div>

              {flags.length > 0 && (
                <section className="flags-section">
                  <div className="section-title"><h3>Control findings</h3><span>{flags.length}</span></div>
                  <div className="flag-list">
                    {flags.map((flag) => (
                      <div className={`flag-row ${flag.severity}`} key={`${flag.code}-${flag.message}`}>
                        {flag.severity === "blocking" ? <XCircle size={17} /> : <AlertTriangle size={17} />}
                        <div><strong>{flag.code.replaceAll("_", " ")}</strong><p>{flag.message}</p></div>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              <section className="timeline-section">
                <div className="section-title"><h3>Live activity</h3><span>{events.length}</span></div>
                {events.length === 0 ? <div className="timeline-empty">Waiting for the first workflow event…</div> : (
                  <ol className="timeline">
                    {[...events].reverse().map((event) => (
                      <li key={event.event_id} className={event.level === "ERROR" ? "error" : ""}>
                        <span className="timeline-icon">{event.status === "FAILED" ? <X size={13} /> : event.event_type === "PROGRESS" ? <Circle size={9} /> : <Check size={12} />}</span>
                        <div><div className="timeline-title"><strong>{event.message}</strong><time>{new Date(event.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</time></div>{eventDetail(event) && <p>{eventDetail(event)}</p>}<small>{event.stage ? displayStage(event.stage) : "Run"} · #{event.sequence}</small></div>
                      </li>
                    ))}
                  </ol>
                )}
              </section>
            </div>
          )}
        </section>

        <aside className="history-panel" aria-labelledby="history-title">
          <div className="panel-heading compact">
            <div><div className="eyebrow">LEDGER</div><h2 id="history-title">Recent runs</h2></div>
            <History size={18} />
          </div>
          {runs.length === 0 ? (
            <div className="history-empty"><span>00</span><p>Completed and pending runs will appear here.</p></div>
          ) : (
            <div className="history-list">
              {runs.map((run) => (
                <button className={`history-row ${selectedRun?.run_id === run.run_id ? "selected" : ""}`} key={run.run_id} onClick={() => void selectRun(run.run_id)}>
                  <span className={`history-status ${statusTone(run.status)}`} />
                  <span className="history-copy"><strong>{run.invoice_id || shortSource(run.source)}</strong><small>{shortSource(run.source)}</small><time>{formatTime(run.created_at)}</time></span>
                  <span className={`history-outcome ${statusTone(run.status)}`}>{run.outcome || statusLabel(run.status)}</span>
                </button>
              ))}
            </div>
          )}
        </aside>
      </main>
    </div>
  );
}

export default App;
