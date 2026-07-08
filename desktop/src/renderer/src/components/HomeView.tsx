import { useState } from 'react'
import type { ProbeResponse } from '../types'
import { baseName, formatTime } from '../util'

interface Props {
  selectedFile: string | null
  probe: ProbeResponse | null
  probing: boolean
  onPickFile: () => void
  onStart: (skipAnalysis: boolean) => void
  starting: boolean
}

export function HomeView({
  selectedFile,
  probe,
  probing,
  onPickFile,
  onStart,
  starting
}: Props): JSX.Element {
  const [skipAnalysis, setSkipAnalysis] = useState(false)

  return (
    <div>
      <div className="header">
        <div>
          <h2>New analysis</h2>
          <div className="sub">Pick a video — ClipMaster transcribes and understands it locally.</div>
        </div>
      </div>

      {!selectedFile ? (
        <div className="dropzone">
          <div className="big">Select a video to begin</div>
          <div className="hint">MP4, MOV, MKV, WEBM, AVI… processed in ≤ 20-minute chunks</div>
          <button className="primary" onClick={onPickFile}>
            Choose file…
          </button>
        </div>
      ) : (
        <>
          <div className="card">
            <h3>Input</h3>
            <div className="kv">
              <span className="k">File</span>
              <span className="mono">{baseName(selectedFile)}</span>
            </div>
            <div className="kv">
              <span className="k">Path</span>
              <span className="mono" style={{ color: 'var(--text-faint)' }}>{selectedFile}</span>
            </div>
            {probing && <div className="empty">Probing media…</div>}
            {probe && (
              <>
                <div className="kv">
                  <span className="k">Duration</span>
                  <span>{formatTime(probe.duration_s)} ({probe.duration_s.toFixed(0)}s)</span>
                </div>
                <div className="kv">
                  <span className="k">Resolution</span>
                  <span>
                    {probe.width && probe.height ? `${probe.width}×${probe.height}` : 'unknown'}
                    {probe.fps ? ` @ ${probe.fps.toFixed(0)} fps` : ''}
                  </span>
                </div>
                <div className="kv">
                  <span className="k">Audio streams</span>
                  <span>{probe.audio_streams}</span>
                </div>
                <div className="kv">
                  <span className="k">Processing chunks</span>
                  <span>{probe.chunk_count} × ≤20 min</span>
                </div>
              </>
            )}
          </div>

          <div className="card">
            <div className="row">
              <label className="checkbox">
                <input
                  type="checkbox"
                  checked={skipAnalysis}
                  onChange={(e) => setSkipAnalysis(e.target.checked)}
                />
                Transcript + silence only (skip LLM analysis)
              </label>
              <div className="spacer" />
              <button onClick={onPickFile}>Change file</button>
              <button className="primary" disabled={starting || !probe} onClick={() => onStart(skipAnalysis)}>
                {starting ? 'Starting…' : 'Start analysis'}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
