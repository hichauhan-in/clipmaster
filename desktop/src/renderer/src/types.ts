// TypeScript mirror of the Python `AnalysisReport` (clipmaster/models.py). Only
// the fields the UI reads are typed; unknown fields are ignored at runtime.

/** Relative weight of each analysis signal (fractions, normalized over enabled). */
export interface SignalWeights {
  transcript: number
  audio: number
  visual: number
}

/** Per-video analysis options chosen on the Home screen before starting. */
export interface AnalyzeOptions {
  skipAnalysis: boolean
  audioEnabled: boolean
  visualEnabled: boolean
  weights: SignalWeights
}

/** Options for the post-analysis action endpoints. */
export interface NotesOptions {
  outputDir?: string | null
  /** Generate Markdown study notes. */
  notes?: boolean
  /** Export the verbatim transcript. */
  transcript?: boolean
  /** Include [mm:ss] timestamps in the exported transcript. */
  transcriptTimestamps?: boolean
}

export interface CleanupOptions {
  outputDir?: string | null
}

export interface ShortsOptions {
  minSeconds: number
  maxSeconds: number
  count?: number | null
  outputDir?: string | null
  /** 'card' = rounded 1:1 card on a canvas; 'fit' = letterbox over blur. */
  style?: 'card' | 'fit'
  /** Card backgrounds to render (card style only): 'blur' and/or 'black'. */
  backgrounds?: ('blur' | 'black')[]
}

export interface VideoStreamInfo {
  codec: string | null
  width: number | null
  height: number | null
  fps: number | null
  bitrate: number | null
}

export interface MediaInfo {
  path: string
  container: string | null
  duration_s: number
  size_bytes: number
  video: VideoStreamInfo | null
  audios: unknown[]
}

export interface Word {
  text: string
  start: number
  end: number
  probability: number | null
}

export interface TranscriptSegment {
  id: number
  start: number
  end: number
  text: string
  words: Word[]
}

export interface Transcript {
  language: string | null
  duration_s: number
  segments: TranscriptSegment[]
}

export interface SilenceSpan {
  start: number
  end: number
}

export type SegmentKind =
  | 'on_topic'
  | 'off_topic'
  | 'qa'
  | 'filler'
  | 'intro'
  | 'outro'
  | 'transition'

export interface SegmentAnalysis {
  segment_id: number
  start: number
  end: number
  kind: SegmentKind
  topic: string | null
  importance: number
  keep: boolean
  reason: string
}

export interface Chapter {
  title: string
  start: number
  end: number
  summary: string
  keywords: string[]
  segment_ids: number[]
}

export interface ClipCandidate {
  title: string
  start: number
  end: number
  score: number
  hook: string
  reason: string
}

export interface KeepSpan {
  start: number
  end: number
  reason: string
}

export interface AnalysisReport {
  schema_version: number
  project_id: string
  source_path: string
  created_at: string
  media: MediaInfo
  transcript: Transcript
  silences: SilenceSpan[]
  summary: string
  keywords: string[]
  chapters: Chapter[]
  segment_analyses: SegmentAnalysis[]
  clip_candidates: ClipCandidate[]
  cleanup_keep_spans: KeepSpan[]
  transcription_model: string
  llm_model: string
  warnings: string[]
}

// --- API DTOs ---------------------------------------------------------------
export interface ComponentStatus {
  name: string
  ok: boolean
  detail: string
}

export interface HealthResponse {
  version: string
  workspace: string
  components: ComponentStatus[]
}

export interface ProbeResponse {
  duration_s: number
  width: number | null
  height: number | null
  fps: number | null
  audio_streams: number
  chunk_count: number
}

export interface ProjectSummary {
  project_id: string
  source_path: string
  created_at: string
  duration_s: number
  chapters: number
  clips: number
  has_transcript: boolean
}

// --- Diagnostics tab --------------------------------------------------------
export interface FixHint {
  winget: string
  url: string
  hint: string
}

export interface DiagnosticsComponent {
  name: string
  category: string
  ok: boolean
  detail: string
  version: string | null
  fix: FixHint | null
}

export interface OllamaModel {
  name: string
  size_bytes: number | null
  family: string | null
  parameter_size: string | null
}

export interface OllamaStatus {
  reachable: boolean
  host: string
  port: number | null
  version: string | null
  models: OllamaModel[]
  selected_model: string
  selected_vision_model: string
  error: string | null
}

export interface PythonInfo {
  version: string
  executable: string
}

export interface LogInfo {
  path: string | null
  level: string
}

export interface DiagnosticsResponse {
  version: string
  workspace: string
  python: PythonInfo
  components: DiagnosticsComponent[]
  ollama: OllamaStatus
  log: LogInfo
}

export interface ActionResult {
  ok: boolean
  message: string
}

export interface JobRef {
  job_id: string
  status: string
}

export interface PullStatus {
  pull_id: string
  model: string
  status: string
  percent: number
  message: string
  done: boolean
  error: string | null
}

export interface LogsResponse {
  path: string | null
  level: string
  lines: string[]
}

// --- Progress events (from the WebSocket) -----------------------------------
export interface ProgressEvent {
  type: string // stage_start | progress | log | stage_end | error | ping | job_done | job_error
  stage?: string
  message?: string
  fraction?: number | null
  data?: Record<string, unknown>
  timestamp?: number
  project_id?: string
  // Present on an action's `job_done` (notes / cleanup / shorts).
  kind?: string
  output_dir?: string
  files?: string[]
}
