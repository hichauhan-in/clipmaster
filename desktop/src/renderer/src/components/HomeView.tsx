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

// The kind of video decides the scoring profile. Educational content leans on
// what is said, so transcript keeps a minimum share of the score (its floor).
// Other profiles are planned and will ship with their own weightings.
const VIDEO_TYPES = [
  {
    id: 'educational',
    label: 'Educational',
    hint: 'Lectures, tutorials, talks',
    available: true,
    transcriptFloor: 30,
    weights: { transcript: 60, audio: 20, visual: 20 }
  },
  {
    id: 'meeting',
    label: 'Meeting',
    hint: 'Calls, standups, reviews',
    available: false,
    transcriptFloor: 0,
    weights: DEFAULT_WEIGHTS
  },
  {
    id: 'gameplay',
    label: 'Gameplay',
    hint: 'Playthroughs, streams',
    available: false,
    transcriptFloor: 0,
    weights: DEFAULT_WEIGHTS
  },
  {
    id: 'podcast',
    label: 'Podcast',
    hint: 'Interviews, chats',
    available: false,
    transcriptFloor: 0,
    weights: DEFAULT_WEIGHTS
  }
] as const

type VideoTypeId = (typeof VIDEO_TYPES)[number]['id']

export function HomeView({
  selectedFile,
  probe,
  probing,
  onPickFile,
  onStart,
  starting
}: Props): JSX.Element {
  const [skipAnalysis, setSkipAnalysis] = useState(false)
  const [videoType, setVideoType] = useState<VideoTypeId>('educational')
  const [audioEnabled, setAudioEnabled] = useState(true)
  const [visualEnabled, setVisualEnabled] = useState(true)
  const [weights, setWeights] = useState(DEFAULT_WEIGHTS)

  const noAudio = probe ? probe.audio_streams === 0 : false
  const audioOn = audioEnabled && !noAudio
  const enabledCount = 1 + (audioOn ? 1 : 0) + (visualEnabled ? 1 : 0)

  const activeType = VIDEO_TYPES.find((t) => t.id === videoType) ?? VIDEO_TYPES[0]
  const transcriptFloor = activeType.transcriptFloor

  // Normalize the raw slider weights across the enabled signals -> shares that
  // sum to 100, then enforce the transcript floor for this video type: if the
  // transcript would fall below it, pin it there and rescale the rest into the
  // remaining room. This is what stops transcript from ever hitting 0%.
  const shares = useMemo(() => {
    const t = weights.transcript
    const a = audioOn ? weights.audio : 0
    const v = visualEnabled ? weights.visual : 0
    const sum = t + a + v
    if (sum <= 0) return { transcript: 100, audio: 0, visual: 0 }
    let tp = (t / sum) * 100
    let ap = (a / sum) * 100
    let vp = (v / sum) * 100
    if (enabledCount > 1 && tp < transcriptFloor) {
      const rest = 100 - transcriptFloor
      const otherSum = ap + vp
      tp = transcriptFloor
      ap = otherSum > 0 ? (ap / otherSum) * rest : 0
      vp = otherSum > 0 ? (vp / otherSum) * rest : 0
    }
    return { transcript: tp, audio: ap, visual: vp }
  }, [weights, audioOn, visualEnabled, enabledCount, transcriptFloor])

  const pct = {
    transcript: Math.round(shares.transcript),
    audio: Math.round(shares.audio),
    visual: Math.round(shares.visual)
  }

  const selectVideoType = (t: (typeof VIDEO_TYPES)[number]): void => {
    if (!t.available) return
    setVideoType(t.id)
    setWeights({ ...t.weights })
  }

  // The sliders show each signal's *share* (0–100). Dragging one sets its share
  // and redistributes the remainder across the other enabled signals in their
  // current proportion, while keeping every signal at or above its floor (the
  // transcript floor for this video type, 0 for the rest). With a single signal
  // there is nothing to balance, so its slider is pinned at 100%.
  const setShare = (signal: 'transcript' | 'audio' | 'visual', nextPct: number): void => {
    if (enabledCount <= 1) return
    const floor = { transcript: transcriptFloor, audio: 0, visual: 0 }
    const enabled: Array<'transcript' | 'audio' | 'visual'> = ['transcript']
    if (audioOn) enabled.push('audio')
    if (visualEnabled) enabled.push('visual')
    const others = enabled.filter((s) => s !== signal)
    const othersFloor = others.reduce((sum, s) => sum + floor[s], 0)
    // The dragged signal ranges from its own floor up to whatever is left once
    // every other enabled signal keeps at least its floor.
    const share = Math.max(floor[signal], Math.min(100 - othersFloor, nextPct))
    const surplus = 100 - share - othersFloor
    const othersTotal = others.reduce((sum, s) => sum + pct[s], 0)
    setWeights((w) => {
      const next = { ...w, [signal]: share }
      others.forEach((s) => {
        const extra = othersTotal > 0 ? surplus * (pct[s] / othersTotal) : surplus / others.length
        next[s] = floor[s] + extra
      })
      return next
    })
  }

  const handleStart = (): void => {
    // Send the floor-respecting shares the UI shows so scoring matches exactly.
    onStart({
      skipAnalysis,
      audioEnabled: audioOn,
      visualEnabled,
      weights: {
        transcript: shares.transcript / 100,
        audio: shares.audio / 100,
        visual: shares.visual / 100
      }
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
                Choose what ClipMaster weighs when scoring each moment. Each slider is that
                signal&apos;s share of the decision; turning one off hands its share to the
                rest, and a lone signal is always 100%.
              </div>
            </div>

            {skipAnalysis ? (
              <div className="empty">
                Scoring is off — “Transcript + silence only” below skips signal analysis.
              </div>
            ) : (
              <>
                <div className="type-tabs">
                  {VIDEO_TYPES.map((t) => (
                    <button
                      key={t.id}
                      type="button"
                      className={`type-tab ${videoType === t.id ? 'active' : ''}`}
                      onClick={() => selectVideoType(t)}
                      disabled={!t.available}
                      title={t.available ? t.hint : 'Coming soon'}
                    >
                      <span className="type-tab-label">{t.label}</span>
                      <span className="type-tab-hint">
                        {t.available ? t.hint : 'Coming soon'}
                      </span>
                      {!t.available && <span className="type-soon">Soon</span>}
                    </button>
                  ))}
                </div>
                {transcriptFloor > 0 && (
                  <p className="type-note">
                    Tuned for {activeType.label.toLowerCase()} content — transcript stays at
                    least {transcriptFloor}% of the score.
                  </p>
                )}
                <div className="signal-list">
                  <SignalRow
                    name="Transcript"
                    desc="What is said — the primary signal."
                    locked
                    enabled
                    adjustable={enabledCount > 1}
                    share={pct.transcript}
                    sliderMin={transcriptFloor}
                    onShare={(v) => setShare('transcript', v)}
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
                    adjustable={audioOn && enabledCount > 1}
                    onToggle={() => setAudioEnabled((v) => !v)}
                    share={pct.audio}
                    sliderMax={100 - transcriptFloor}
                    onShare={(v) => setShare('audio', v)}
                  />
                  <SignalRow
                    name="On-screen visuals"
                    desc="Slides, code, demos, hardware — read by the local vision model."
                    enabled={visualEnabled}
                    adjustable={visualEnabled && enabledCount > 1}
                    onToggle={() => setVisualEnabled((v) => !v)}
                    share={pct.visual}
                    sliderMax={100 - transcriptFloor}
                    onShare={(v) => setShare('visual', v)}
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
  share: number
  onShare: (v: number) => void
  adjustable: boolean
  sliderMin?: number
  sliderMax?: number
  locked?: boolean
  disabledReason?: boolean
  onToggle?: () => void
}

function SignalRow({
  name,
  desc,
  enabled,
  share,
  onShare,
  adjustable,
  sliderMin = 0,
  sliderMax = 100,
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
        min={sliderMin}
        max={sliderMax}
        step={5}
        value={enabled ? share : sliderMin}
        disabled={!adjustable}
        onChange={(e) => onShare(Number(e.target.value))}
        aria-label={`${name} share`}
        title={
          adjustable
            ? `${name} share`
            : enabled
              ? 'Only active signal — 100%'
              : `${name} is off`
        }
      />
      <span className="signal-pct">{enabled ? `${share}%` : 'Off'}</span>
    </div>
  )
}
