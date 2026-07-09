import { useEffect, useMemo, useState } from 'react'
import type { KeyboardEvent } from 'react'
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
  // Numeric fields are edited as free text (so select-all + retype works) and
  // clamped/reconciled on blur.
  const [minDraft, setMinDraft] = useState('15')
  const [maxDraft, setMaxDraft] = useState('30')
  const [countDraft, setCountDraft] = useState('6')
  // Shorts framing: rounded "card" (over blur/black) or fitted "fit", and the
  // output aspect (9:16 vertical or 16:9 horizontal).
  const [shortStyle, setShortStyle] = useState<'card' | 'fit'>('card')
  const [aspect, setAspect] = useState<'9:16' | '16:9'>('9:16')
  // Card backgrounds are independent toggles — pick blurred, black, or both.
  const [wantBlur, setWantBlur] = useState(true)
  const [wantBlack, setWantBlack] = useState(false)
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
    setMinDraft('15')
    setMaxDraft('30')
    setCountDraft('6')
    setShortStyle('card')
    setAspect('9:16')
    setWantBlur(true)
    setWantBlack(false)
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

  // Card backgrounds selected as an ordered list (blur first) for the request.
  const cardBackgrounds: ('blur' | 'black')[] = []
  if (wantBlur) cardBackgrounds.push('blur')
  if (wantBlack) cardBackgrounds.push('black')

  // Commit a numeric field on blur: clamp to bounds and keep min <= max.
  const commitMin = (): void => {
    let n = parseInt(minDraft, 10)
    if (Number.isNaN(n)) n = range[0]
    n = Math.max(3, Math.min(180, n))
    const hi = Math.max(n, range[1])
    setRange([n, hi])
    setMinDraft(String(n))
    setMaxDraft(String(hi))
  }
  const commitMax = (): void => {
    let n = parseInt(maxDraft, 10)
    if (Number.isNaN(n)) n = range[1]
    n = Math.max(3, Math.min(180, n))
    const lo = Math.min(n, range[0])
    setRange([lo, n])
    setMinDraft(String(lo))
    setMaxDraft(String(n))
  }
  const commitCount = (): void => {
    let n = parseInt(countDraft, 10)
    if (Number.isNaN(n)) n = count
    n = Math.max(1, Math.min(30, n))
    setCount(n)
    setCountDraft(String(n))
  }
  const blurOnEnter = (e: KeyboardEvent<HTMLInputElement>): void => {
    if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
  }

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
      else if (action === 'cleanup')
        ref = await client.makeCleanup(report.project_id, { outputDir: folder })
      else
        ref = await client.makeShorts(report.project_id, {
          minSeconds: range[0],
          maxSeconds: range[1],
          count,
          style: shortStyle,
          aspect,
          backgrounds: cardBackgrounds
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

            {action === 'shorts' && (
              <>
                <p className="action-lead">
                  Cut short clips from the best moments. Choose the aspect and template,
                  then a soft length range — each short is fit within it.
                </p>
                <div className="field">
                  <label>Aspect ratio</label>
                  <div className="preset-row">
                    <button
                      className={`preset ${aspect === '9:16' ? 'active' : ''}`}
                      onClick={() => setAspect('9:16')}
                    >
                      9:16 · vertical
                    </button>
                    <button
                      className={`preset ${aspect === '16:9' ? 'active' : ''}`}
                      onClick={() => setAspect('16:9')}
                    >
                      16:9 · horizontal
                    </button>
                  </div>
                </div>
                <div className="field">
                  <label>Template</label>
                  <div className="preset-row">
                    <button
                      className={`preset ${shortStyle === 'card' ? 'active' : ''}`}
                      onClick={() => setShortStyle('card')}
                    >
                      Card · rounded
                    </button>
                    <button
                      className={`preset ${shortStyle === 'fit' ? 'active' : ''}`}
                      onClick={() => setShortStyle('fit')}
                    >
                      Fit · full frame
                    </button>
                  </div>
                </div>
                {shortStyle === 'card' && (
                  <div className="field">
                    <label>Card background</label>
                    <div className="preset-row">
                      <button
                        className={`preset ${wantBlur ? 'active' : ''}`}
                        onClick={() => setWantBlur((v) => !v)}
                      >
                        Blurred
                      </button>
                      <button
                        className={`preset ${wantBlack ? 'active' : ''}`}
                        onClick={() => setWantBlack((v) => !v)}
                      >
                        Black
                      </button>
                    </div>
                    {cardBackgrounds.length === 0 && (
                      <div className="banner warn" style={{ marginTop: 4 }}>
                        Pick at least one background to continue.
                      </div>
                    )}
                    {cardBackgrounds.length === 2 && (
                      <p className="field-hint">
                        Each moment is rendered twice — one blurred and one black.
                      </p>
                    )}
                  </div>
                )}
                <div className="preset-row">
                  {SHORT_PRESETS.map((p) => {
                    const active = range[0] === p.min && range[1] === p.max
                    return (
                      <button
                        key={p.label}
                        className={`preset ${active ? 'active' : ''}`}
                        onClick={() => {
                          setRange([p.min, p.max])
                          setMinDraft(String(p.min))
                          setMaxDraft(String(p.max))
                        }}
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
                      inputMode="numeric"
                      min={3}
                      max={180}
                      value={minDraft}
                      onChange={(e) => setMinDraft(e.target.value)}
                      onBlur={commitMin}
                      onKeyDown={blurOnEnter}
                    />
                  </div>
                  <div className="field">
                    <label>Max seconds</label>
                    <input
                      type="number"
                      inputMode="numeric"
                      min={3}
                      max={180}
                      value={maxDraft}
                      onChange={(e) => setMaxDraft(e.target.value)}
                      onBlur={commitMax}
                      onKeyDown={blurOnEnter}
                    />
                  </div>
                  <div className="field">
                    <label>How many</label>
                    <input
                      type="number"
                      inputMode="numeric"
                      min={1}
                      max={30}
                      value={countDraft}
                      onChange={(e) => setCountDraft(e.target.value)}
                      onBlur={commitCount}
                      onKeyDown={blurOnEnter}
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
                  (action === 'notes' && !wantNotes && !wantTranscript) ||
                  (action === 'shorts' && shortStyle === 'card' && cardBackgrounds.length === 0)
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
