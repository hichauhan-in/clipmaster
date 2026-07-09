import type {
  ActionResult,
  AnalysisReport,
  AnalyzeOptions,
  CleanupOptions,
  DiagnosticsResponse,
  HealthResponse,
  JobRef,
  LogsResponse,
  NotesOptions,
  ProbeResponse,
  ProjectSummary,
  PullStatus,
  ShortsOptions
} from '../types'

// The backend URL is provided by the Electron main process. We resolve it once
// and cache the promise so every call shares the same lookup.
let backendUrlPromise: Promise<string> | null = null

export function backendUrl(): Promise<string> {
  if (!backendUrlPromise) {
    backendUrlPromise = window.clipmaster
      ? window.clipmaster.getBackendUrl()
      : Promise.resolve('http://127.0.0.1:8756')
  }
  return backendUrlPromise
}

async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const base = await backendUrl()
  const res = await fetch(`${base}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...init
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status} ${res.statusText}${text ? `: ${text}` : ''}`)
  }
  return (await res.json()) as T
}

export const client = {
  health: () => api<HealthResponse>('/api/health'),

  probe: (path: string) =>
    api<ProbeResponse>('/api/probe', {
      method: 'POST',
      body: JSON.stringify({ path })
    }),

  analyze: (path: string, opts: AnalyzeOptions) =>
    api<{ job_id: string; status: string }>('/api/analyze', {
      method: 'POST',
      body: JSON.stringify({
        path,
        skip_analysis: opts.skipAnalysis,
        audio_enabled: opts.audioEnabled,
        visual_enabled: opts.visualEnabled,
        weights: opts.weights
      })
    }),

  projects: () => api<ProjectSummary[]>('/api/projects'),

  project: (id: string) => api<AnalysisReport>(`/api/projects/${id}`),

  deleteProject: (id: string) =>
    api<ActionResult>(`/api/projects/${id}`, { method: 'DELETE' }),

  // --- Post-analysis actions (return a job to stream over the WebSocket) -----
  makeNotes: (id: string, opts: NotesOptions) =>
    api<JobRef>(`/api/projects/${id}/notes`, {
      method: 'POST',
      body: JSON.stringify({
        output_dir: opts.outputDir ?? null,
        notes: opts.notes ?? true,
        transcript: opts.transcript ?? false,
        transcript_timestamps: opts.transcriptTimestamps ?? true
      })
    }),

  makeCleanup: (id: string, opts: CleanupOptions = {}) =>
    api<JobRef>(`/api/projects/${id}/cleanup`, {
      method: 'POST',
      body: JSON.stringify({ output_dir: opts.outputDir ?? null })
    }),

  makeShorts: (id: string, opts: ShortsOptions) =>
    api<JobRef>(`/api/projects/${id}/shorts`, {
      method: 'POST',
      body: JSON.stringify({
        min_seconds: opts.minSeconds,
        max_seconds: opts.maxSeconds,
        count: opts.count ?? null,
        output_dir: opts.outputDir ?? null,
        style: opts.style ?? 'card',
        backgrounds: opts.backgrounds ?? ['blur']
      })
    }),

  // --- Diagnostics ----------------------------------------------------------
  diagnostics: () => api<DiagnosticsResponse>('/api/diagnostics'),

  startOllama: () => api<ActionResult>('/api/ollama/start', { method: 'POST' }),

  selectModel: (model: string) =>
    api<ActionResult>('/api/settings/model', {
      method: 'POST',
      body: JSON.stringify({ model })
    }),

  selectVisionModel: (model: string) =>
    api<ActionResult>('/api/settings/vision-model', {
      method: 'POST',
      body: JSON.stringify({ model })
    }),

  pullModel: (model: string) =>
    api<PullStatus>('/api/ollama/pull', {
      method: 'POST',
      body: JSON.stringify({ model })
    }),

  pullStatus: (pullId: string) => api<PullStatus>(`/api/ollama/pull/${pullId}`),

  logs: (limit = 200) => api<LogsResponse>(`/api/logs?limit=${limit}`),

  setLogPath: (path: string) =>
    api<ActionResult>('/api/logs/path', {
      method: 'POST',
      body: JSON.stringify({ path })
    }),

  async wsUrl(jobId: string): Promise<string> {
    const base = await backendUrl()
    return `${base.replace(/^http/, 'ws')}/ws/jobs/${jobId}`
  }
}
