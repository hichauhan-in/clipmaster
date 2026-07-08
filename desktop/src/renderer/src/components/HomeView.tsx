import { useMemo, useState } from 'react'
import type { AnalyzeOptions, ProbeResponse } from '../types'
import { baseName, formatTime } from '../util'

interface Props {
  selectedFile: string | null
  probe: ProbeResponse | null
  probing: boolean
  onPickFile: () => void
  onStart: (opts: AnalyzeOptions) => void
  starting: boolean
}

// Raw slider values (0–100). The displayed contribution of each signal is these
// weights normalized across the *enabled* signals, which mirrors how the backend
// fuses them, so the percentages the user sees are what actually gets applied.
const DEFAULT_WEIGHTS = { transcript: 60, audio: 20, visual: 20 }

export function HomeView({
  selectedFile,
  probe,
  probing,
  onPickFile,
  onStart,
  starting
}: Props): JSX.Element {
  const [skipAnalysis, setSkipAnalysis] = useState(false)
  const [audioEnabled, setAudioEnabled] = useState(true)
  const [visualEnabled, setVisualEnabled] = useState(true)
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)

  const noAudio = probe ? probe.audio_streams === 0 : false
  const audioOn = audioEnabled && !noAudio

  // Normalize the raw slider weights across the enabled signals -> percentages.
  const pct = useMemo(() => {
    const t = weights.transcript
    const a = audioOn ? weights.audio : 0
    const v = visualEnabled ? weights.visual : 0
    const sum = t + a + v
    if (sum <= 0) return { transcript: 100, audio: 0, visual: 0 }
    return {
      transcript: Math.round((t / sum) * 100),
      audio: Math.round((a / sum) * 100),
      visual: Math.round((v / sum) * 100)
    }
  }, [weights, audioOn, visualEnabled])

  const handleStart = (): void => {
    // Send the same normalized fractions the UI shows so scoring matches.
    const t = weights.transcript
    const a = audioOn ? weights.audio : 0
    const v = visualEnabled ? weights.visual : 0
    const sum = t + a + v || 1
    onStart({
      skipAnalysis,
      audioEnabled: audioOn,
      visualEnabled,
      weights: { transcript: t / sum, audio: a / sum, visual: v / sum }
    })
  }

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

          {/* Analysis signals ------------------------------------------------ */}
          <div className={`card signals-card ${skipAnalysis ? 'disabled' : ''}`}>
            <div className="signals-head">
              <h3>Analysis signals</h3>
              <div className="sub">
                Choose what ClipMaster weighs when scoring each moment. Percentages are each
                enabled signal&apos;s share of the decision.
              </div>
            </div>

            {skipAnalysis ? (
              <div className="empty">
                Scoring is off — “Transcript + silence only” below skips signal analysis.
              </div>
            ) : (
              <>
                <div className="signal-list">
                  <SignalRow
                    name="Transcript"
                    desc="What is said — the primary signal."
                    locked
                    enabled
                    value={weights.transcript}
                    pct={pct.transcript}
                    onValue={(v) => setWeights((w) => ({ ...w, transcript: v }))}
                  />
                  <SignalRow
                    name="Audio delivery"
                    desc={
                      noAudio
                        ? 'No audio track in this file.'
                        : 'Loudness, pace and pauses — how it’s said.'
                    }
                    enabled={audioOn}
                    disabledReason={noAudio}
                    onToggle={() => setAudioEnabled((v) => !v)}
                    value={weights.audio}
                    pct={pct.audio}
                    onValue={(v) => setWeights((w) => ({ ...w, audio: v }))}
                  />
                  <SignalRow
                    name="On-screen visuals"
                    desc="Slides, code, demos, hardware — read by the local vision model."
                    enabled={visualEnabled}
                    onToggle={() => setVisualEnabled((v) => !v)}
                    value={weights.visual}
                    pct={pct.visual}
                    onValue={(v) => setWeights((w) => ({ ...w, visual: v }))}
                  />
                </div>

                <div className="signal-balance">
                  <span className="k">Balance</span>
                  <span className="signal-share">Transcript {pct.transcript}%</span>
                  {audioOn && <span className="signal-share">Audio {pct.audio}%</span>}
                  {visualEnabled && <span className="signal-share">Visual {pct.visual}%</span>}
                  {!visualEnabled && (
                    <span className="signal-note">
                      Vision off — relying on transcript{audioOn ? ' + audio' : ''} only (faster).
                    </span>
                  )}
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
              <button className="primary" disabled={starting || !probe} onClick={handleStart}>
                {starting ? 'Starting…' : 'Start analysis'}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

interface SignalRowProps {
  name: string
  desc: string
  enabled: boolean
  value: number
  pct: number
  onValue: (v: number) => void
  locked?: boolean
  disabledReason?: boolean
  onToggle?: () => void
}

function SignalRow({
  name,
  desc,
  enabled,
  value,
  pct,
  onValue,
  locked,
  disabledReason,
  onToggle
}: SignalRowProps): JSX.Element {
  return (
    <div className={`signal-row ${enabled ? '' : 'off'}`}>
      <button
        type="button"
        className={`switch ${enabled ? 'on' : ''}`}
        onClick={onToggle}
        disabled={locked || disabledReason}
        aria-pressed={enabled}
        aria-label={`Toggle ${name}`}
        title={locked ? 'Always on — the base signal' : `Toggle ${name}`}
      >
        <span className="switch-knob" />
      </button>
      <div className="signal-info">
        <div className="signal-name">
          {name}
          {locked && <span className="signal-tag">always on</span>}
        </div>
        <div className="signal-desc">{desc}</div>
      </div>
      <input
        className="signal-slider"
        type="range"
        min={0}
        max={100}
        step={5}
        value={value}
        disabled={!enabled}
        onChange={(e) => onValue(Number(e.target.value))}
        aria-label={`${name} weight`}
      />
      <span className="signal-pct">{enabled ? `${pct}%` : 'Off'}</span>
    </div>
  )
}
