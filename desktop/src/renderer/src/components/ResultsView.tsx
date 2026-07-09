import { useMemo, useState } from 'react'
import type { AnalysisReport, SegmentKind } from '../types'
import { baseName, formatTime, humanSize, kindColor } from '../util'
import { ActionModal, type ActionKind } from './ActionModal'

interface Props {
  report: AnalysisReport
  workspace: string | null
  onNotify: (message: string) => void
}

export function ResultsView({ report, workspace, onNotify }: Props): JSX.Element {
  const duration = report.media.duration_s || 1
  const [action, setAction] = useState<ActionKind | null>(null)
  const kindBySegment = useMemo(() => {
    const map = new Map<number, SegmentKind>()
    for (const a of report.segment_analyses) map.set(a.segment_id, a.kind)
    return map
  }, [report])

  const keptSeconds = report.cleanup_keep_spans.reduce((acc, s) => acc + (s.end - s.start), 0)
  const removedSeconds = Math.max(0, report.media.duration_s - keptSeconds)
  const removedPct = report.media.duration_s ? Math.round((removedSeconds / report.media.duration_s) * 100) : 0

  const openFolder = () => {
    if (workspace) window.clipmaster.openPath(`${workspace}/${report.project_id}`)
    else onNotify('Workspace path unknown')
  }

  return (
    <div>
      <div className="header">
        <div>
          <h2>{baseName(report.source_path)}</h2>
          <div className="sub">
            {formatTime(report.media.duration_s)} · {report.chapters.length} chapters ·{' '}
            {report.clip_candidates.length} clip candidates
          </div>
        </div>
        <button onClick={openFolder}>Open project folder</button>
      </div>

      {report.warnings.length > 0 && (
        <div className="banner warn">{report.warnings.join(' · ')}</div>
      )}

      {/* --- What next: the per-phase actions --- */}
      <div className="card">
        <h3>What would you like to do?</h3>
        <div className="actionbar">
          <button className="action" onClick={() => setAction('notes')}>
            <div className="t">Notes &amp; summary</div>
            <div className="d">Study notes with diagrams, or export the raw transcript.</div>
          </button>
          <button className="action" onClick={() => setAction('cleanup')}>
            <div className="t">Clean up</div>
            <div className="d">Remove silence, filler, off-topic and ads to a trimmed cut.</div>
          </button>
          <button className="action" onClick={() => setAction('shorts')}>
            <div className="t">Make shorts</div>
            <div className="d">Cut vertical 9:16 clips from the best moments.</div>
          </button>
          <button className="action" disabled>
            <span className="soon">soon</span>
            <div className="t">Edit</div>
            <div className="d">Add intro/outro banners and text from templates.</div>
          </button>
        </div>
      </div>

      <ActionModal
        action={action}
        report={report}
        onClose={() => setAction(null)}
        onNotify={onNotify}
      />

      {report.summary && (
        <div className="card">
          <h3>Summary</h3>
          <p style={{ margin: 0 }}>{report.summary}</p>
          {report.keywords.length > 0 && (
            <div className="chips" style={{ marginTop: 12 }}>
              {report.keywords.map((k) => (
                <span key={k} className="chip">{k}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* --- Timeline --- */}
      <div className="card">
        <h3>Timeline</h3>
        <div className="timeline">
          {report.chapters.map((c, i) => (
            <div
              key={i}
              className="chapter"
              title={`${c.title} (${formatTime(c.start)}–${formatTime(c.end)})`}
              style={{
                left: `${(c.start / duration) * 100}%`,
                width: `${((c.end - c.start) / duration) * 100}%`,
                background: i % 2 ? 'var(--accent-dim)' : '#233047'
              }}
            >
              {c.title}
            </div>
          ))}
          {report.silences.map((s, i) => (
            <div
              key={`s${i}`}
              className="silence"
              title={`silence ${formatTime(s.start)}–${formatTime(s.end)}`}
              style={{
                left: `${(s.start / duration) * 100}%`,
                width: `${Math.max(0.15, ((s.end - s.start) / duration) * 100)}%`
              }}
            />
          ))}
        </div>
        <div className="sub" style={{ marginTop: 8, fontSize: 12 }}>
          Blue blocks = chapters · red = detected silence
        </div>
      </div>

      <div className="grid-2">
        {/* --- Media --- */}
        <div className="card">
          <h3>Media</h3>
          <div className="kv"><span className="k">Duration</span><span>{formatTime(report.media.duration_s)}</span></div>
          <div className="kv">
            <span className="k">Resolution</span>
            <span>
              {report.media.video?.width
                ? `${report.media.video.width}×${report.media.video.height}`
                : 'unknown'}
              {report.media.video?.fps ? ` @ ${report.media.video.fps.toFixed(0)} fps` : ''}
            </span>
          </div>
          <div className="kv"><span className="k">Container</span><span>{report.media.container ?? 'unknown'}</span></div>
          <div className="kv"><span className="k">Size</span><span>{humanSize(report.media.size_bytes)}</span></div>
          <div className="kv"><span className="k">Transcription</span><span className="mono">{report.transcription_model || 'n/a'}</span></div>
          <div className="kv"><span className="k">LLM</span><span className="mono">{report.llm_model || 'heuristics only'}</span></div>
        </div>

        {/* --- Cleanup preview --- */}
        <div className="card">
          <h3>Cleanup preview</h3>
          <div className="kv"><span className="k">Kept</span><span>{formatTime(keptSeconds)}</span></div>
          <div className="kv"><span className="k">Removed</span><span>{formatTime(removedSeconds)} ({removedPct}% shorter)</span></div>
          <div className="kv"><span className="k">Silent spans</span><span>{report.silences.length}</span></div>
          <div className="kv">
            <span className="k">Flagged segments</span>
            <span>
              {report.segment_analyses.filter((s) => s.kind === 'filler').length} filler ·{' '}
              {report.segment_analyses.filter((s) => s.kind === 'off_topic').length} off-topic ·{' '}
              {report.segment_analyses.filter((s) => s.kind === 'qa').length} Q&amp;A
            </span>
          </div>
        </div>
      </div>

      {/* --- Chapters --- */}
      {report.chapters.length > 0 && (
        <div className="card">
          <h3>Chapters</h3>
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr><th style={{ width: 120 }}>Time</th><th>Title</th><th>Summary</th></tr>
              </thead>
              <tbody>
                {report.chapters.map((c, i) => (
                  <tr key={i}>
                    <td className="mono">{formatTime(c.start)}–{formatTime(c.end)}</td>
                    <td>{c.title}</td>
                    <td style={{ color: 'var(--text-dim)' }}>{c.summary}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* --- Clip candidates --- */}
      {report.clip_candidates.length > 0 && (
        <div className="card">
          <h3>Suggested clips</h3>
          <div className="table-scroll">
            <table className="data">
              <thead>
                <tr><th>Score</th><th>Time</th><th>Len</th><th>Title</th><th>Hook</th></tr>
              </thead>
              <tbody>
                {report.clip_candidates.map((c, i) => (
                  <tr key={i}>
                    <td><span className="score">{c.score.toFixed(2)}</span></td>
                    <td className="mono">{formatTime(c.start)}</td>
                    <td>{Math.round(c.end - c.start)}s</td>
                    <td>{c.title}</td>
                    <td style={{ color: 'var(--text-dim)' }}>{c.hook}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* --- Transcript --- */}
      {report.transcript.segments.length > 0 && (
        <div className="card">
          <h3>Transcript</h3>
          <div className="transcript">
            {report.transcript.segments.map((seg) => {
              const kind = kindBySegment.get(seg.id) ?? 'on_topic'
              return (
                <div key={seg.id} className="seg">
                  <span className="t">{formatTime(seg.start)}</span>
                  <span className="kindbar" style={{ background: kindColor(kind) }} title={kind} />
                  <span>{seg.text}</span>
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
