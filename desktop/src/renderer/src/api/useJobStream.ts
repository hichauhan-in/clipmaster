import { useEffect, useRef, useState } from 'react'
import { client } from './client'
import type { ProgressEvent } from '../types'

export interface StageStatus {
  stage: string
  status: 'active' | 'complete' | 'error'
  message: string
  fraction: number | null
}

export interface JobStreamState {
  events: ProgressEvent[]
  stages: StageStatus[]
  currentMessage: string
  fraction: number | null
  done: boolean
  error: string | null
  projectId: string | null
  connected: boolean
  // Populated from an action's `job_done` (notes / cleanup / shorts).
  outputDir: string | null
  files: string[]
  doneMessage: string | null
}

const INITIAL: JobStreamState = {
  events: [],
  stages: [],
  currentMessage: '',
  fraction: null,
  done: false,
  error: null,
  projectId: null,
  connected: false,
  outputDir: null,
  files: [],
  doneMessage: null
}

/**
 * Subscribe to a job's live progress WebSocket. Re-runs when `jobId` changes;
 * closes the socket on cleanup. Stage order is preserved as stages are seen.
 */
export function useJobStream(jobId: string | null): JobStreamState {
  const [state, setState] = useState<JobStreamState>(INITIAL)
  const socketRef = useRef<WebSocket | null>(null)

  useEffect(() => {
    if (!jobId) {
      setState(INITIAL)
      return
    }
    setState({ ...INITIAL })
    let cancelled = false

    const apply = (ev: ProgressEvent) => {
      setState((prev) => reduce(prev, ev))
    }

    client.wsUrl(jobId).then((url) => {
      if (cancelled) return
      const ws = new WebSocket(url)
      socketRef.current = ws
      ws.onopen = () => setState((p) => ({ ...p, connected: true }))
      ws.onmessage = (msg) => {
        try {
          apply(JSON.parse(msg.data) as ProgressEvent)
        } catch {
          /* ignore malformed frames */
        }
      }
      ws.onclose = () => setState((p) => ({ ...p, connected: false }))
      ws.onerror = () =>
        setState((p) => ({ ...p, error: p.error ?? 'WebSocket connection error' }))
    })

    return () => {
      cancelled = true
      socketRef.current?.close()
      socketRef.current = null
    }
  }, [jobId])

  return state
}

function reduce(prev: JobStreamState, ev: ProgressEvent): JobStreamState {
  if (ev.type === 'ping') return prev
  if (ev.type === 'job_done') {
    return {
      ...prev,
      done: true,
      fraction: 1,
      projectId: ev.project_id ?? prev.projectId,
      outputDir: ev.output_dir ?? prev.outputDir,
      files: ev.files ?? prev.files,
      doneMessage: ev.message ?? prev.doneMessage
    }
  }
  if (ev.type === 'job_error') {
    return { ...prev, error: ev.message ?? 'Job failed', done: true }
  }

  const events = [...prev.events, ev]
  let stages = prev.stages
  const stageName = ev.stage
  if (stageName) {
    const idx = stages.findIndex((s) => s.stage === stageName)
    const next: StageStatus = {
      stage: stageName,
      status:
        ev.type === 'stage_end'
          ? 'complete'
          : ev.type === 'error'
            ? 'error'
            : 'active',
      message: ev.message ?? (idx >= 0 ? stages[idx].message : ''),
      fraction: ev.fraction ?? (idx >= 0 ? stages[idx].fraction : null)
    }
    stages = idx >= 0 ? stages.map((s, i) => (i === idx ? next : s)) : [...stages, next]
  }

  return {
    ...prev,
    events,
    stages,
    currentMessage: ev.message ?? prev.currentMessage,
    fraction: ev.fraction ?? prev.fraction,
    error: ev.type === 'error' ? ev.message ?? prev.error : prev.error
  }
}
