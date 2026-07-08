import type { HealthResponse, ProjectSummary } from '../types'
import { baseName, formatTime } from '../util'
import { GearIcon, LogoMark, TrashIcon } from './icons'

interface Props {
  view: string
  settingsOpen: boolean
  onNewAnalysis: () => void
  onOpenSettings: () => void
  health: HealthResponse | null
  projects: ProjectSummary[]
  activeProjectId: string | null
  onOpenProject: (id: string) => void
  onDeleteProject: (project: ProjectSummary) => void
}

export function Sidebar({
  view,
  settingsOpen,
  onNewAnalysis,
  onOpenSettings,
  health,
  projects,
  activeProjectId,
  onOpenProject,
  onDeleteProject
}: Props): JSX.Element {
  const homeActive = view === 'home'
  const issues = health ? health.components.filter((c) => !c.ok).length : 0
  const statusLabel = !health
    ? 'Connecting to backend…'
    : issues === 0
      ? 'All systems ready'
      : `${issues} item${issues > 1 ? 's' : ''} need attention`
  const statusClass = !health ? '' : issues === 0 ? 'ok' : 'warn'

  return (
    <aside className="sidebar">
      <div className="brand">
        <LogoMark size={30} />
        <div className="brand-text">
          <h1>ClipMaster</h1>
          <small>{health ? `v${health.version}` : 'connecting…'}</small>
        </div>
      </div>

      <div className="nav">
        <button className={`nav-item ${homeActive ? 'active' : ''}`} onClick={onNewAnalysis}>
          <span className="plus">+</span>
          New analysis
        </button>
      </div>

      <div className="section-label">Projects</div>
      <div className="project-list">
        {projects.length === 0 && (
          <div className="empty" style={{ paddingLeft: 10 }}>No projects yet</div>
        )}
        {projects.map((p) => (
          <div
            key={p.project_id}
            className={`project-item ${activeProjectId === p.project_id ? 'active' : ''}`}
            onClick={() => onOpenProject(p.project_id)}
          >
            <div className="project-info">
              <div className="name">{baseName(p.source_path)}</div>
              <div className="meta">
                {formatTime(p.duration_s)} · {p.chapters} ch · {p.clips} clips
              </div>
            </div>
            <button
              className="project-del"
              title="Delete project"
              aria-label={`Delete ${baseName(p.source_path)}`}
              onClick={(e) => {
                e.stopPropagation()
                onDeleteProject(p)
              }}
            >
              <TrashIcon size={15} />
            </button>
          </div>
        ))}
      </div>

      <div className="sidebar-footer">
        <button
          className={`settings-icon-btn ${settingsOpen ? 'active' : ''}`}
          onClick={onOpenSettings}
          title={`Settings — ${statusLabel}`}
          aria-label="Open settings"
        >
          <GearIcon size={18} />
          {statusClass !== 'ok' && (
            <span className={`status-dot ${statusClass}`} aria-hidden="true" />
          )}
        </button>
      </div>
    </aside>
  )
}
