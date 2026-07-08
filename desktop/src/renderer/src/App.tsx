import { useCallback, useEffect, useRef, useState } from 'react'
import { client } from './api/client'
import { Sidebar } from './components/Sidebar'
import { HomeView } from './components/HomeView'
import { ProcessingView } from './components/ProcessingView'
import { ResultsView } from './components/ResultsView'
import { DiagnosticsView } from './components/DiagnosticsView'
import type { AnalysisReport, HealthResponse, ProbeResponse, ProjectSummary } from './types'

type View = 'home' | 'processing' | 'results' | 'diagnostics'

export default function App(): JSX.Element {
  const [view, setView] = useState<View>('home')
  const [health, setHealth] = useState<HealthResponse | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])

  const [selectedFile, setSelectedFile] = useState<string | null>(null)
  const [probe, setProbe] = useState<ProbeResponse | null>(null)
  const [probing, setProbing] = useState(false)
  const [starting, setStarting] = useState(false)

  const [jobId, setJobId] = useState<string | null>(null)
  const [jobFilePath, setJobFilePath] = useState<string>('')

  const [report, setReport] = useState<AnalysisReport | null>(null)
  const [activeProjectId, setActiveProjectId] = useState<string | null>(null)

  const [toast, setToast] = useState<string | null>(null)
  const toastTimer = useRef<number | null>(null)

  const notify = useCallback((message: string) => {
    setToast(message)
    if (toastTimer.current) window.clearTimeout(toastTimer.current)
    toastTimer.current = window.setTimeout(() => setToast(null), 4000)
  }, [])

  const refreshProjects = useCallback(async () => {
    try {
      setProjects(await client.projects())
    } catch {
      /* backend not ready yet */
    }
  }, [])

  // Poll health until the backend (Python sidecar) is reachable.
  useEffect(() => {
    let cancelled = false
    let timer: number
    const poll = async () => {
      try {
        const h = await client.health()
        if (cancelled) return
        setHealth(h)
        refreshProjects()
        timer = window.setTimeout(poll, 15000) // slow keep-alive once connected
      } catch {
        if (cancelled) return
        timer = window.setTimeout(poll, 1500) // retry while starting up
      }
    }
    poll()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
    }
  }, [refreshProjects])

  const probeFile = useCallback(
    async (path: string) => {
      setProbing(true)
      setProbe(null)
      try {
        setProbe(await client.probe(path))
      } catch (e) {
        notify(`Could not read file: ${(e as Error).message}`)
      } finally {
        setProbing(false)
      }
    },
    [notify]
  )

  const pickFile = useCallback(async () => {
    const path = await window.clipmaster.selectVideoFile()
    if (!path) return
    setSelectedFile(path)
    probeFile(path)
  }, [probeFile])

  const startAnalyze = useCallback(
    async (skipAnalysis: boolean) => {
      if (!selectedFile) return
      setStarting(true)
      try {
        const { job_id } = await client.analyze(selectedFile, skipAnalysis)
        setJobId(job_id)
        setJobFilePath(selectedFile)
        setView('processing')
      } catch (e) {
        notify(`Failed to start: ${(e as Error).message}`)
      } finally {
        setStarting(false)
      }
    },
    [selectedFile, notify]
  )

  const loadProject = useCallback(
    async (id: string) => {
      try {
        const r = await client.project(id)
        setReport(r)
        setActiveProjectId(id)
        setView('results')
      } catch (e) {
        notify(`Could not load project: ${(e as Error).message}`)
      }
    },
    [notify]
  )

  const onJobDone = useCallback(
    (projectId: string) => {
      refreshProjects()
      loadProject(projectId)
    },
    [refreshProjects, loadProject]
  )

  const goHome = useCallback(() => {
    setSelectedFile(null)
    setProbe(null)
    setJobId(null)
    setView('home')
  }, [])

  const navigate = useCallback(
    (target: 'home' | 'diagnostics') => {
      if (target === 'diagnostics') setView('diagnostics')
      else goHome()
    },
    [goHome]
  )

  return (
    <div className="app">
      <Sidebar
        view={view}
        onNavigate={navigate}
        health={health}
        projects={projects}
        activeProjectId={activeProjectId}
        onOpenProject={loadProject}
      />
      <main className="main">
        <div className="main-inner">
          {view === 'home' && (
            <HomeView
              selectedFile={selectedFile}
              probe={probe}
              probing={probing}
              onPickFile={pickFile}
              onStart={startAnalyze}
              starting={starting}
            />
          )}
          {view === 'diagnostics' && <DiagnosticsView onNotify={notify} />}
          {view === 'processing' && jobId && (
            <ProcessingView
              jobId={jobId}
              filePath={jobFilePath}
              onDone={onJobDone}
              onBack={goHome}
            />
          )}
          {view === 'results' && report && (
            <ResultsView report={report} workspace={health?.workspace ?? null} onNotify={notify} />
          )}
        </div>
      </main>

      {toast && (
        <div
          style={{
            position: 'fixed',
            bottom: 20,
            left: '50%',
            transform: 'translateX(-50%)',
            background: 'var(--bg-elev-2)',
            border: '1px solid var(--border-strong)',
            borderRadius: 8,
            padding: '10px 16px',
            boxShadow: 'var(--shadow)',
            zIndex: 100
          }}
        >
          {toast}
        </div>
      )}
    </div>
  )
}
