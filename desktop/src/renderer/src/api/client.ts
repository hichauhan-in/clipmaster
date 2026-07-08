import type {
  AnalysisReport,
  HealthResponse,
  ProbeResponse,
  ProjectSummary
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

  analyze: (path: string, skipAnalysis: boolean) =>
    api<{ job_id: string; status: string }>('/api/analyze', {
      method: 'POST',
      body: JSON.stringify({ path, skip_analysis: skipAnalysis })
    }),

  projects: () => api<ProjectSummary[]>('/api/projects'),

  project: (id: string) => api<AnalysisReport>(`/api/projects/${id}`),

  async wsUrl(jobId: string): Promise<string> {
    const base = await backendUrl()
    return `${base.replace(/^http/, 'ws')}/ws/jobs/${jobId}`
  }
}
