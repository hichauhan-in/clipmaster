import { useCallback, useEffect, useRef, useState } from 'react'
import { client } from '../api/client'
import { humanSize } from '../util'
import type { DiagnosticsComponent, DiagnosticsResponse, PullStatus } from '../types'

interface Props {
  onNotify: (message: string) => void
}

export function DiagnosticsView({ onNotify }: Props): JSX.Element {
  const [diag, setDiag] = useState<DiagnosticsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [busy, setBusy] = useState<string | null>(null)
  const [pullName, setPullName] = useState('')
  const [pull, setPull] = useState<PullStatus | null>(null)
  const [logLines, setLogLines] = useState<string[]>([])
  const [logPath, setLogPath] = useState<string | null>(null)
  const pullTimer = useRef<number | null>(null)
  const logBoxRef = useRef<HTMLDivElement | null>(null)

  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      setDiag(await client.diagnostics())
    } catch (e) {
      onNotify(`Diagnostics unavailable: ${(e as Error).message}`)
    } finally {
      setLoading(false)
    }
  }, [onNotify])

  const refreshLogs = useCallback(async () => {
    try {
      const l = await client.logs(400)
      setLogLines(l.lines)
      setLogPath(l.path)
    } catch {
      /* backend not ready */
    }
  }, [])

  useEffect(() => {
    refresh()
    refreshLogs()
    const t = window.setInterval(refreshLogs, 3000)
    return () => window.clearInterval(t)
  }, [refresh, refreshLogs])

  // Keep the log viewer pinned to the newest line.
  useEffect(() => {
    const el = logBoxRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [logLines])

  useEffect(
    () => () => {
      if (pullTimer.current) window.clearInterval(pullTimer.current)
    },
    []
  )

  const copy = useCallback(
    (text: string) => {
      navigator.clipboard.writeText(text).then(
        () => onNotify('Copied to clipboard'),
        () => onNotify('Could not copy')
      )
    },
    [onNotify]
  )

  const startOllama = useCallback(async () => {
    setBusy('ollama')
    try {
      const r = await client.startOllama()
      onNotify(r.message)
      await refresh()
    } catch (e) {
      onNotify(`Could not start Ollama: ${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }, [onNotify, refresh])

  const useModel = useCallback(
    async (model: string) => {
      setBusy(`use:${model}`)
      try {
        const r = await client.selectModel(model)
        onNotify(r.message)
        await refresh()
      } catch (e) {
        onNotify((e as Error).message)
      } finally {
        setBusy(null)
      }
    },
    [onNotify, refresh]
  )

  const pollPull = useCallback(
    (id: string) => {
      if (pullTimer.current) window.clearInterval(pullTimer.current)
      pullTimer.current = window.setInterval(async () => {
        try {
          const s = await client.pullStatus(id)
          setPull(s)
          if (s.done) {
            if (pullTimer.current) window.clearInterval(pullTimer.current)
            pullTimer.current = null
            onNotify(s.error ?? `${s.model} is ready`)
            refresh()
          }
        } catch {
          /* keep polling */
        }
      }, 700)
    },
    [onNotify, refresh]
  )

  const startPull = useCallback(async () => {
    const model = pullName.trim()
    if (!model) return
    setBusy('pull')
    try {
      const s = await client.pullModel(model)
      setPull(s)
      pollPull(s.pull_id)
    } catch (e) {
      onNotify(`Pull failed: ${(e as Error).message}`)
    } finally {
      setBusy(null)
    }
  }, [pullName, pollPull, onNotify])

  const chooseLogFolder = useCallback(async () => {
    const dir = await window.clipmaster.selectFolder()
    if (!dir) return
    try {
      const r = await client.setLogPath(dir)
      onNotify(r.message)
      refreshLogs()
    } catch (e) {
      onNotify((e as Error).message)
    }
  }, [onNotify, refreshLogs])

  const ollama = diag?.ollama
  const pullActive = pull !== null && !pull.done

  return (
    <>
      <div className="settings-toolbar">
        <span className="settings-sub">
          Dependencies, local AI models and logs — everything the app needs to run.
        </span>
        <span className="spacer" />
        <button onClick={refresh} disabled={loading}>
          {loading ? 'Refreshing…' : 'Refresh'}
        </button>
      </div>

      {/* Dependencies -------------------------------------------------------- */}
      <div className="card">
        <h3>Dependencies</h3>
        {!diag && <div className="empty">Checking the environment…</div>}
        {diag?.components.map((c) => (
          <DependencyRow key={c.name} comp={c} onCopy={copy} />
        ))}
        {diag && (
          <div className="diag-python">
            Python {diag.python.version} · <span className="mono">{diag.python.executable}</span>
          </div>
        )}
      </div>

      {/* Ollama -------------------------------------------------------------- */}
      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Ollama (local AI)</h3>
          <span className={`badge ${ollama?.reachable ? 'ok' : 'bad'}`}>
            <span className="dot" />
            {ollama?.reachable ? 'running' : 'offline'}
          </span>
          <div className="spacer" />
          {!ollama?.reachable && (
            <button className="primary" onClick={startOllama} disabled={busy === 'ollama'}>
              {busy === 'ollama' ? 'Starting…' : 'Start Ollama'}
            </button>
          )}
        </div>

        <div className="kv">
          <span className="k">Server</span>
          <span className="mono">{ollama?.host ?? '—'}</span>
        </div>
        <div className="kv">
          <span className="k">Port</span>
          <span className="mono">{ollama?.port ?? '—'}</span>
        </div>
        <div className="kv">
          <span className="k">Version</span>
          <span className="mono">{ollama?.version ?? '—'}</span>
        </div>
        <div className="kv">
          <span className="k">Active model</span>
          <span className="mono">{ollama?.selected_model ?? '—'}</span>
        </div>

        {ollama?.reachable && (
          <>
            <div className="section-head">Installed models — pick the one to use</div>
            {ollama.models.length === 0 && (
              <div className="empty">No models yet. Pull one below (e.g. llama3.1:8b).</div>
            )}
            <div className="model-list">
              {ollama.models.map((m) => {
                const selected = m.name === ollama.selected_model
                return (
                  <div key={m.name} className={`model-row ${selected ? 'selected' : ''}`}>
                    <div className="model-info">
                      <div className="model-name">{m.name}</div>
                      <div className="model-meta">
                        {m.parameter_size ? `${m.parameter_size} · ` : ''}
                        {m.family ? `${m.family} · ` : ''}
                        {m.size_bytes ? humanSize(m.size_bytes) : ''}
                      </div>
                    </div>
                    {selected ? (
                      <span className="chip in-use">In use</span>
                    ) : (
                      <button
                        onClick={() => useModel(m.name)}
                        disabled={busy === `use:${m.name}`}
                      >
                        Use
                      </button>
                    )}
                  </div>
                )
              })}
            </div>

            <div className="section-head">Pull a new model</div>
            <div className="row">
              <input
                className="text-input"
                placeholder="e.g. llama3.1:8b, llava:13b, qwen2.5:7b"
                value={pullName}
                onChange={(e) => setPullName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && !pullActive && startPull()}
                disabled={pullActive}
              />
              <button
                className="primary"
                onClick={startPull}
                disabled={pullActive || busy === 'pull' || !pullName.trim()}
              >
                {pullActive ? 'Pulling…' : 'Pull'}
              </button>
            </div>
            {pull && (
              <div className="pull-status">
                <div className="progressbar">
                  <div style={{ width: `${pull.error ? 0 : pull.percent}%` }} />
                </div>
                <div className={`pull-msg ${pull.error ? 'err' : ''}`}>
                  {pull.model}: {pull.message}
                </div>
              </div>
            )}
          </>
        )}
        {ollama && !ollama.reachable && ollama.error && (
          <div className="diag-error">{ollama.error}</div>
        )}
      </div>

      {/* Logs ---------------------------------------------------------------- */}
      <div className="card">
        <div className="row" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Logs & issues</h3>
          <div className="spacer" />
          <button onClick={chooseLogFolder}>Change folder…</button>
          {logPath && <button onClick={() => window.clipmaster.openPath(logPath)}>Open folder</button>}
        </div>
        <div className="kv">
          <span className="k">Log file</span>
          <span className="mono">{logPath ?? 'not set — choose a folder to persist logs'}</span>
        </div>
        <div className="log log-viewer" ref={logBoxRef}>
          {logLines.length === 0 && <div className="line dim">No log entries yet.</div>}
          {logLines.map((line, i) => (
            <div key={i} className={`line ${/ERROR|WARN/i.test(line) ? 'err' : ''}`}>
              {line}
            </div>
          ))}
        </div>
      </div>
    </>
  )
}

function DependencyRow({
  comp,
  onCopy
}: {
  comp: DiagnosticsComponent
  onCopy: (t: string) => void
}): JSX.Element {
  return (
    <div className="diag-row">
      <span className={`dep-dot ${comp.ok ? 'ok' : 'bad'}`} />
      <div className="dep-body">
        <div className="dep-title">
          <span className="dep-name">{comp.name}</span>
          <span className="dep-cat">{comp.category}</span>
        </div>
        <div className="dep-detail">{comp.detail}</div>
        {comp.fix && (
          <div className="dep-fix">
            {comp.fix.hint && <div className="fix-hint">{comp.fix.hint}</div>}
            <div className="fix-actions">
              {comp.fix.winget && (
                <>
                  <code className="fix-cmd">{comp.fix.winget}</code>
                  <button className="mini" onClick={() => onCopy(comp.fix!.winget)}>
                    Copy
                  </button>
                </>
              )}
              {comp.fix.url && (
                <button className="mini" onClick={() => window.clipmaster.openExternal(comp.fix!.url)}>
                  Download page
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
