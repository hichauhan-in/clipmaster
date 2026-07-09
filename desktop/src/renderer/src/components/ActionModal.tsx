import { useEffect, useMemo, useState } from 'react'
import { client } from '../api/client'
import { useJobStream } from '../api/useJobStream'
import type { AnalysisReport } from '../types'
import { baseName, formatTime } from '../util'
import { Modal } from './Modal'
import { CheckIcon, FilmIcon, FolderIcon, NotesIcon, ScissorsIcon } from './icons'

export type ActionKind = 'notes' | 'cleanup' | 'shorts'

interface Props {
  action: ActionKind | null
  report: AnalysisReport
  onClose: () => void
  onNotify: (message: string) => void
}

const META: Record<ActionKind, { title: string; icon: JSX.Element; verb: string }> = {
  notes: { title: 'Notes & summary', icon: <NotesIcon size={16} />, verb: 'Generate notes' },
  cleanup: { title: 'Clean up', icon: <ScissorsIcon size={16} />, verb: 'Clean up video' },
  shorts: { title: 'Make shorts', icon: <FilmIcon size={16} />, verb: 'Make shorts' }
}

const SHORT_PRESETS: { label: string; min: number; max: number }[] = [
  { label: 'Quick · 10–20s', min: 10, max: 20 },
  { label: 'Standard · 15–30s', min: 15, max: 30 },
  { label: 'Long · 30–60s', min: 30, max: 60 }
]

export function ActionModal({ action, report, onClose, onNotify }: Props): JSX.Element | null {
  const [jobId, setJobId] = useState<string | null>(null)
  const [starting, setStarting] = useState(false)

  // Inputs
  const [folder, setFolder] = useState<string | null>(null)
  const [range, setRange] = useState<[number, number]>([15, 30])
  const [count, setCount] = useState(6)
  // Notes & summary: choose notes, transcript, or both.
  const [wantNotes, setWantNotes] = useState(true)
  const [wantTranscript, setWantTranscript] = useState(false)
  const [transcriptTimestamps, setTranscriptTimestamps] = useState(true)

  const stream = useJobStream(jobId)

  // Reset everything whenever the chosen action changes (or the modal closes).
  useEffect(() => {
    setJobId(null)
    setStarting(false)
    setFolder(null)
    setRange([15, 30])
    setCount(6)
    setWantNotes(true)
    setWantTranscript(false)
    setTranscriptTimestamps(true)
  }, [action])

  const cleanup = useMemo(() => {
    const kept = report.cleanup_keep_spans.reduce((a, s) => a + (s.end - s.start), 0)
    const removed = Math.max(0, report.media.duration_s - kept)
    const pct = report.media.duration_s ? Math.round((removed / report.media.duration_s) * 100) : 0
    return { kept, removed, pct }
  }, [report])

  if (!action) return null

  const meta = META[action]
  const phase = stream.error
    ? 'error'
    : stream.done
      ? 'done'
      : jobId
        ? 'running'
        : 'setup'

  // Notes action can produce notes and/or a transcript — label the button for it.
  const notesVerb =
    wantNotes && wantTranscript
      ? 'Generate notes + transcript'
      : wantTranscript
        ? 'Export transcript'
        : 'Generate notes'

  const chooseFolder = async (): Promise<void> => {
    const picked = await window.clipmaster.selectFolder()
    if (picked) setFolder(picked)
  }

  const start = async (): Promise<void> => {
    setStarting(true)
    try {
      let ref
      if (action === 'notes')
        ref = await client.makeNotes(report.project_id, {
          outputDir: folder,
          notes: wantNotes,
          transcript: wantTranscript,
          transcriptTimestamps
        })
      else if (action === 'cleanup') ref = await client.makeCleanup(report.project_id, {})
      else
        ref = await client.makeShorts(report.project_id, {
          minSeconds: range[0],
          maxSeconds: range[1],
          count
        })
      setJobId(ref.job_id)
    } catch (e) {
      onNotify(`Could not start: ${(e as Error).message}`)
    } finally {
      setStarting(false)
    }
  }

  const retry = (): void => setJobId(null)
  const openOutput = (): void => {
    if (stream.outputDir) window.clipmaster.openPath(stream.outputDir)
  }

  const pct = stream.fraction != null ? Math.round(stream.fraction * 100) : null

  return (
    <Modal open onClose={onClose} title={meta.title} icon={meta.icon}>
      <div className="action-modal">
        {phase === 'setup' && (
          <div className="action-setup">
            {action === 'notes' && (
              <>
                <p className="action-lead">
                  Choose what to produce for{' '}
                  <strong>{baseName(report.source_path)}</strong>. Pick study notes,
                  the raw transcript, or both.
                </p>
                <div className="toggle-list">
                  <label className={`toggle-card ${wantNotes ? 'on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={wantNotes}
                      onChange={(e) => setWantNotes(e.target.checked)}
                    />
                    <span className="toggle-body">
                      <span className="toggle-title">
                        <NotesIcon size={15} /> Study notes
                      </span>
                      <span className="toggle-desc">
                        Structured Markdown written from the transcript and screenshots —
                        summaries, key points and mermaid diagrams.
                      </span>
                    </span>
                  </label>
                  <label className={`toggle-card ${wantTranscript ? 'on' : ''}`}>
                    <input
                      type="checkbox"
                      checked={wantTranscript}
                      onChange={(e) => setWantTranscript(e.target.checked)}
                    />
                    <span className="toggle-body">
                      <span className="toggle-title">
                        <NotesIcon size={15} /> Transcript
                      </span>
                      <span className="toggle-desc">
                        The verbatim words as <span className="mono">transcript.md</span> and{' '}
                        <span className="mono">transcript.txt</span> — no rewriting.
                      </span>
                      {wantTranscript && (
                        <label className="sub-toggle">
                          <input
                            type="checkbox"
                            checked={transcriptTimestamps}
                            onChange={(e) => setTranscriptTimestamps(e.target.checked)}
                          />
                          Include timestamps
                        </label>
                      )}
                    </span>
                  </label>
                </div>
                {!wantNotes && !wantTranscript && (
                  <div className="banner warn" style={{ marginTop: 4 }}>
                    Select at least one output to continue.
                  </div>
                )}
                <div className="field">
                  <label>Save to</label>
                  <div className="folder-pick">
                    <span className="folder-path mono">
                      {folder ?? 'Project folder (default)'}
                    </span>
                    <button className="ghost" onClick={chooseFolder}>
                      <FolderIcon size={15} /> Choose…
                    </button>
                  </div>
                  {folder && (
                    <button className="link-btn" onClick={() => setFolder(null)}>
                      Reset to project folder
                    </button>
                  )}
                </div>
              </>
            )}

            {action === 'cleanup' && (
              <>
                <p className="action-lead">
                  Render a tighter cut of <strong>{baseName(report.source_path)}</strong>,
                  removing silence, filler, off-topic asides and self-promotion / ads
                  (course plugs, sponsor reads, subscribe CTAs). On-screen demos and
                  navigation are always kept.
                </p>
                <div className="stat-row">
                  <div className="stat">
                    <div className="stat-v">{formatTime(cleanup.kept)}</div>
                    <div className="stat-l">Kept</div>
                  </div>
                  <div className="stat">
                    <div className="stat-v">{formatTime(cleanup.removed)}</div>
                    <div className="stat-l">Removed</div>
                  </div>
                  <div className="stat">
                    <div className="stat-v">{cleanup.pct}%</div>
                    <div className="stat-l">Shorter</div>
                  </div>
                </div>
                {report.cleanup_keep_spans.length === 0 && (
                  <div className="banner warn" style={{ marginTop: 12 }}>
                    No cleanup plan on this project — re-run the analysis with content
                    understanding enabled first.
                  </div>
                )}
              </>
            )}

            {action === 'shorts' && (
              <>
                <p className="action-lead">
                  Cut vertical 9:16 shorts from the best moments. Pick a soft length range —
                  each short is fit within it.
                </p>
                <div className="preset-row">
                  {SHORT_PRESETS.map((p) => {
                    const active = range[0] === p.min && range[1] === p.max
                    return (
                      <button
                        key={p.label}
                        className={`preset ${active ? 'active' : ''}`}
                        onClick={() => setRange([p.min, p.max])}
                      >
                        {p.label}
                      </button>
                    )
                  })}
                </div>
                <div className="field-row">
                  <div className="field">
                    <label>Min seconds</label>
                    <input
                      type="number"
                      min={3}
                      max={range[1]}
                      value={range[0]}
                      onChange={(e) =>
                        setRange([Math.min(Number(e.target.value), range[1]), range[1]])
                      }
                    />
                  </div>
                  <div className="field">
                    <label>Max seconds</label>
                    <input
                      type="number"
                      min={range[0]}
                      max={180}
                      value={range[1]}
                      onChange={(e) =>
                        setRange([range[0], Math.max(Number(e.target.value), range[0])])
                      }
                    />
                  </div>
                  <div className="field">
                    <label>How many</label>
                    <input
                      type="number"
                      min={1}
                      max={30}
                      value={count}
                      onChange={(e) => setCount(Math.max(1, Math.min(30, Number(e.target.value))))}
                    />
                  </div>
                </div>
              </>
            )}

            <div className="action-actions">
              <button onClick={onClose} disabled={starting}>
                Cancel
              </button>
              <button
                className="primary"
                onClick={start}
                disabled={
                  starting ||
                  (action === 'cleanup' && report.cleanup_keep_spans.length === 0) ||
                  (action === 'notes' && !wantNotes && !wantTranscript)
                }
              >
                {starting ? 'Starting…' : action === 'notes' ? notesVerb : meta.verb}
              </button>
            </div>
          </div>
        )}

        {phase === 'running' && (
          <div className="action-running">
            <p className="action-lead">Working… you can keep this open.</p>
            <div className="progressbar">
              <div style={{ width: `${pct ?? 5}%` }} />
            </div>
            <div className="run-msg mono">
              {stream.currentMessage || (stream.connected ? 'Starting…' : 'Connecting…')}
              {pct != null ? ` · ${pct}%` : ''}
            </div>
          </div>
        )}

        {phase === 'done' && (
          <div className="action-done">
            <div className="done-head">
              <span className="done-ico">
                <CheckIcon size={22} />
              </span>
              <div>
                <div className="done-title">All done</div>
                <div className="done-msg">{stream.doneMessage ?? 'Finished.'}</div>
              </div>
            </div>
            {stream.files.length > 0 && (
              <div className="file-list">
                {stream.files.map((f) => (
                  <div key={f} className="file-item mono" title={f}>
                    {baseName(f)}
                  </div>
                ))}
              </div>
            )}
            <div className="action-actions">
              <button onClick={onClose}>Close</button>
              <button className="primary" onClick={openOutput} disabled={!stream.outputDir}>
                <FolderIcon size={15} /> Open folder
              </button>
            </div>
          </div>
        )}

        {phase === 'error' && (
          <div className="action-error">
            <div className="banner bad">{stream.error}</div>
            <div className="action-actions">
              <button onClick={onClose}>Close</button>
              <button className="primary" onClick={retry}>
                Try again
              </button>
            </div>
          </div>
        )}
      </div>
    </Modal>
  )
}
