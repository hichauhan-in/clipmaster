import { useEffect, useRef } from 'react'
import { useJobStream } from '../api/useJobStream'
import { baseName } from '../util'

interface Props {
  jobId: string
  filePath: string
  onDone: (projectId: string) => void
  onBack: () => void
}

export function ProcessingView({ jobId, filePath, onDone, onBack }: Props): JSX.Element {
  const stream = useJobStream(jobId)
  const logRef = useRef<HTMLDivElement>(null)
  const firedRef = useRef(false)

  useEffect(() => {
    if (stream.done && stream.projectId && !firedRef.current) {
      firedRef.current = true
      onDone(stream.projectId)
    }
  }, [stream.done, stream.projectId, onDone])

  useEffect(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight })
  }, [stream.events.length])

  const pct =
    stream.fraction != null ? Math.round(stream.fraction * 100) : stream.done ? 100 : null

  return (
    <div>
      <div className="header">
        <div>
          <h2>Analyzing</h2>
          <div className="sub mono">{baseName(filePath)}</div>
        </div>
        <button onClick={onBack}>← Back</button>
      </div>

      {stream.error && <div className="banner bad">Analysis failed: {stream.error}</div>}

      <div className="card">
        <h3>Pipeline</h3>
        {pct != null && (
          <div className="progressbar">
            <div style={{ width: `${pct}%` }} />
          </div>
        )}
        {stream.stages.length === 0 && !stream.error && (
          <div className="empty">{stream.connected ? 'Waiting for the pipeline…' : 'Connecting…'}</div>
        )}
        {stream.stages.map((s) => (
          <div key={s.stage} className={`stage ${s.status}`}>
            <span className="icon" />
            <span className="label">{s.stage.replace(/_/g, ' ')}</span>
            <span className="msg">
              {s.message}
              {s.fraction != null && s.status === 'active' ? ` — ${Math.round(s.fraction * 100)}%` : ''}
            </span>
          </div>
        ))}
      </div>

      <div className="card">
        <h3>Live log</h3>
        <div className="log" ref={logRef}>
          {stream.events
            .filter((e) => e.stage)
            .map((e, i) => (
              <div key={i} className="line">
                <span style={{ color: 'var(--text-faint)' }}>[{e.stage}] </span>
                {e.type === 'progress' && e.fraction != null
                  ? `${Math.round(e.fraction * 100)}% `
                  : ''}
                {e.message}
              </div>
            ))}
        </div>
      </div>
    </div>
  )
}
