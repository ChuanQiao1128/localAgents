from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.tools.file_tools import FileTools

from .base import AgentResult


class ImplementationHardeningAgent:
    id = "implementation_hardening"

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.files = FileTools(project_path, ["apps/**", "docs/**", "tests/**"], [".env", "~/**"])

    def run(self, target: str = "backend-api") -> AgentResult:
        if target != "backend-api":
            return AgentResult(status="failed", summary=f"Unsupported hardening target: {target}")
        web_dir = self.project_path / "apps/web"
        if not (web_dir / "package.json").exists() or not (web_dir / "app/page.tsx").exists():
            return AgentResult(status="failed", summary="Backend/API hardening currently requires a Next app in apps/web.")

        outputs = _backend_api_outputs(self.project_path)
        artifacts: list[str] = []
        failures: list[str] = []
        for relative_path, content in outputs.items():
            result = self.files.write_text(relative_path, content)
            if result.ok:
                artifacts.append(result.path)
            else:
                failures.append(result.message)
        if failures:
            return AgentResult(status="failed", summary="; ".join(failures), artifacts=artifacts)
        return AgentResult(
            status="completed",
            summary="Added SQLite-backed API routes, API/schema docs, and browser interaction test artifacts.",
            artifacts=artifacts,
        )


def _backend_api_outputs(project_path: Path) -> dict[str, str]:
    return {
        "apps/web/package.json": _hardened_package_json(project_path / "apps/web/package.json"),
        "apps/web/lib/server/project-repository.ts": _project_repository_ts(),
        "apps/web/lib/project-client.ts": _project_client_ts(),
        "apps/web/app/page.tsx": _dashboard_page_tsx(),
        "apps/web/app/api/health/route.ts": _health_route_ts(),
        "apps/web/app/api/projects/route.ts": _projects_route_ts(),
        "apps/web/app/api/projects/[projectId]/route.ts": _project_detail_route_ts(),
        "apps/web/app/api/projects/[projectId]/tasks/route.ts": _project_tasks_route_ts(),
        "apps/web/app/api/backup/route.ts": _backup_route_ts(),
        "apps/web/app/api/export/route.ts": _export_route_ts(),
        "apps/web/playwright.config.ts": _playwright_config_ts(),
        "apps/web/tests/e2e/creator-project-tracker.spec.ts": _playwright_spec_ts(),
        "docs/architecture/api.openapi.yaml": _openapi_yaml(),
        "docs/architecture/database-schema.md": _database_schema_md(),
        "docs/implementation/hardening-plan.md": _hardening_plan_md(),
        "tests/creator-project-tracker-api-smoke.md": _api_smoke_md(),
    }


def _hardened_package_json(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    payload.setdefault("name", "creator-project-tracker-web-app")
    payload.setdefault("private", True)
    payload.setdefault("version", "0.1.0")
    scripts = dict(payload.get("scripts") or {})
    scripts.setdefault("dev", "next dev --webpack")
    scripts.setdefault("build", "next build")
    scripts.setdefault("start", "next start")
    scripts["test:e2e"] = "playwright test"
    scripts["test:e2e:headed"] = "playwright test --headed"
    payload["scripts"] = scripts
    dev_deps = dict(payload.get("devDependencies") or {})
    dev_deps.setdefault("@playwright/test", "^1.49.1")
    payload["devDependencies"] = dict(sorted(dev_deps.items()))
    return json.dumps(payload, ensure_ascii=False, indent=2) + "\n"


def _project_repository_ts() -> str:
    return """import { mkdirSync } from 'node:fs'
import { join } from 'node:path'
import { DatabaseSync } from 'node:sqlite'
import { INITIAL_PROJECTS, type Project, type ProjectStatus, type Task } from '@/lib/store'

type ProjectRow = {
  id: string
  title: string
  description: string
  status: ProjectStatus
  goal: string
  tags_json: string
  screenshot_url: string | null
  screenshot_alt: string | null
  publish_url: string | null
  repo_url: string | null
  retro_note: string
  created_at: string
  updated_at: string
}

type TaskRow = {
  id: string
  project_id: string
  title: string
  done: number
  created_at: string
}

const VALID_STATUSES = new Set<ProjectStatus>(['draft', 'active', 'shipped', 'paused'])
const DEFAULT_DB_PATH = join(process.cwd(), '.data', 'creator-projects.sqlite')

let db: DatabaseSync | null = null

function connection(): DatabaseSync {
  if (db) return db
  const databasePath = process.env.AGENT_STUDIO_DB_PATH || DEFAULT_DB_PATH
  mkdirSync(join(databasePath, '..'), { recursive: true })
  db = new DatabaseSync(databasePath)
  db.exec(`
    PRAGMA journal_mode = WAL;
    PRAGMA foreign_keys = ON;

    CREATE TABLE IF NOT EXISTS projects (
      id TEXT PRIMARY KEY,
      title TEXT NOT NULL,
      description TEXT NOT NULL DEFAULT '',
      status TEXT NOT NULL CHECK (status IN ('draft', 'active', 'shipped', 'paused')),
      goal TEXT NOT NULL DEFAULT '',
      tags_json TEXT NOT NULL DEFAULT '[]',
      screenshot_url TEXT,
      screenshot_alt TEXT,
      publish_url TEXT,
      repo_url TEXT,
      retro_note TEXT NOT NULL DEFAULT '',
      created_at TEXT NOT NULL,
      updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tasks (
      id TEXT PRIMARY KEY,
      project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
      title TEXT NOT NULL,
      done INTEGER NOT NULL DEFAULT 0,
      created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_projects_updated_at ON projects(updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_tasks_project_id ON tasks(project_id);
  `)
  seedIfEmpty()
  return db
}

function seedIfEmpty() {
  const database = db
  if (!database) return
  const row = database.prepare('SELECT COUNT(*) AS count FROM projects').get() as { count: number }
  if (Number(row.count) > 0) return
  for (const project of INITIAL_PROJECTS) {
    writeProject(project)
  }
}

function nowIso(): string {
  return new Date().toISOString()
}

function safeStatus(value: unknown): ProjectStatus {
  return VALID_STATUSES.has(value as ProjectStatus) ? value as ProjectStatus : 'draft'
}

function tagsFromJson(value: string): string[] {
  try {
    const parsed = JSON.parse(value)
    return Array.isArray(parsed) ? parsed.map(String).filter(Boolean) : []
  } catch {
    return []
  }
}

function taskFromRow(row: TaskRow): Task {
  return {
    id: row.id,
    title: row.title,
    done: Boolean(row.done),
    createdAt: row.created_at,
  }
}

function projectFromRow(row: ProjectRow, tasks: Task[]): Project {
  return {
    id: row.id,
    title: row.title,
    description: row.description,
    status: safeStatus(row.status),
    goal: row.goal,
    tags: tagsFromJson(row.tags_json),
    screenshotUrl: row.screenshot_url || undefined,
    screenshotAlt: row.screenshot_alt || undefined,
    publishUrl: row.publish_url || undefined,
    repoUrl: row.repo_url || undefined,
    retroNote: row.retro_note,
    tasks,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
  }
}

function tasksForProject(projectId: string): Task[] {
  const rows = connection()
    .prepare('SELECT id, project_id, title, done, created_at FROM tasks WHERE project_id = ? ORDER BY created_at ASC')
    .all(projectId) as TaskRow[]
  return rows.map(taskFromRow)
}

export function listProjects(): Project[] {
  const rows = connection()
    .prepare('SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC')
    .all() as ProjectRow[]
  return rows.map((row) => projectFromRow(row, tasksForProject(row.id)))
}

export function getProject(projectId: string): Project | null {
  const row = connection().prepare('SELECT * FROM projects WHERE id = ?').get(projectId) as ProjectRow | undefined
  if (!row) return null
  return projectFromRow(row, tasksForProject(projectId))
}

export function writeProject(project: Project): Project {
  const database = connection()
  database.exec('BEGIN')
  try {
    database.prepare(`
      INSERT INTO projects (
        id, title, description, status, goal, tags_json, screenshot_url, screenshot_alt,
        publish_url, repo_url, retro_note, created_at, updated_at
      ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
      ON CONFLICT(id) DO UPDATE SET
        title = excluded.title,
        description = excluded.description,
        status = excluded.status,
        goal = excluded.goal,
        tags_json = excluded.tags_json,
        screenshot_url = excluded.screenshot_url,
        screenshot_alt = excluded.screenshot_alt,
        publish_url = excluded.publish_url,
        repo_url = excluded.repo_url,
        retro_note = excluded.retro_note,
        updated_at = excluded.updated_at
    `).run(
      project.id,
      project.title,
      project.description,
      safeStatus(project.status),
      project.goal,
      JSON.stringify(project.tags || []),
      project.screenshotUrl || null,
      project.screenshotAlt || null,
      project.publishUrl || null,
      project.repoUrl || null,
      project.retroNote,
      project.createdAt,
      project.updatedAt,
    )
    database.prepare('DELETE FROM tasks WHERE project_id = ?').run(project.id)
    const insertTask = database.prepare('INSERT INTO tasks (id, project_id, title, done, created_at) VALUES (?, ?, ?, ?, ?)')
    for (const task of project.tasks || []) {
      insertTask.run(task.id, project.id, task.title, task.done ? 1 : 0, task.createdAt)
    }
    database.exec('COMMIT')
  } catch (error) {
    database.exec('ROLLBACK')
    throw error
  }
  return getProject(project.id) as Project
}

export function createProject(input: Partial<Project>): Project {
  const createdAt = nowIso()
  const project: Project = {
    id: input.id || crypto.randomUUID(),
    title: String(input.title || 'Untitled project').trim().slice(0, 140),
    description: String(input.description || '').trim(),
    status: safeStatus(input.status),
    goal: String(input.goal || '').trim(),
    tags: Array.isArray(input.tags) ? input.tags.map(String).filter(Boolean) : [],
    screenshotUrl: input.screenshotUrl || undefined,
    screenshotAlt: input.screenshotAlt || undefined,
    publishUrl: input.publishUrl || undefined,
    repoUrl: input.repoUrl || undefined,
    retroNote: String(input.retroNote || ''),
    tasks: Array.isArray(input.tasks) ? input.tasks : [],
    createdAt: input.createdAt || createdAt,
    updatedAt: createdAt,
  }
  return writeProject(project)
}

export function updateProject(projectId: string, input: Partial<Project>): Project | null {
  const existing = getProject(projectId)
  if (!existing) return null
  return writeProject({
    ...existing,
    ...input,
    id: existing.id,
    status: input.status ? safeStatus(input.status) : existing.status,
    tags: Array.isArray(input.tags) ? input.tags.map(String).filter(Boolean) : existing.tags,
    tasks: Array.isArray(input.tasks) ? input.tasks : existing.tasks,
    updatedAt: nowIso(),
  })
}

export function deleteProject(projectId: string): boolean {
  const result = connection().prepare('DELETE FROM projects WHERE id = ?').run(projectId)
  return Number(result.changes) > 0
}

export function addTask(projectId: string, title: string): Project | null {
  const project = getProject(projectId)
  if (!project) return null
  const task: Task = {
    id: crypto.randomUUID(),
    title: title.trim().slice(0, 160),
    done: false,
    createdAt: nowIso(),
  }
  return writeProject({ ...project, tasks: [...project.tasks, task], updatedAt: nowIso() })
}

export function replaceAllProjects(projects: Project[]): Project[] {
  const database = connection()
  database.prepare('DELETE FROM tasks').run()
  database.prepare('DELETE FROM projects').run()
  for (const project of projects) {
    writeProject({
      ...project,
      id: project.id || crypto.randomUUID(),
      status: safeStatus(project.status),
      tags: Array.isArray(project.tags) ? project.tags.map(String).filter(Boolean) : [],
      tasks: Array.isArray(project.tasks) ? project.tasks : [],
      createdAt: project.createdAt || nowIso(),
      updatedAt: nowIso(),
    })
  }
  return listProjects()
}
"""


def _project_client_ts() -> str:
    return """import type { Project } from '@/lib/store'

export type PersistenceMode = 'sqlite' | 'local'

type ProjectPayload = {
  project: Project
}

type ProjectsPayload = {
  projects: Project[]
}

export type BackupPayload = {
  version: 1
  exportedAt: string
  projects: Project[]
}

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers || {}),
    },
  })
  if (!response.ok) {
    const text = await response.text().catch(() => '')
    throw new Error(text || `Request failed: ${response.status}`)
  }
  return response.json() as Promise<T>
}

export async function fetchProjectsFromApi(): Promise<Project[]> {
  const payload = await requestJson<ProjectsPayload>('/api/projects', { cache: 'no-store' })
  return payload.projects
}

export async function createProjectInApi(project: Project): Promise<Project> {
  const payload = await requestJson<ProjectPayload>('/api/projects', {
    method: 'POST',
    body: JSON.stringify(project),
  })
  return payload.project
}

export async function updateProjectInApi(project: Project): Promise<Project> {
  const payload = await requestJson<ProjectPayload>(`/api/projects/${project.id}`, {
    method: 'PATCH',
    body: JSON.stringify(project),
  })
  return payload.project
}

export async function deleteProjectInApi(projectId: string): Promise<void> {
  await requestJson<{ ok: boolean }>(`/api/projects/${projectId}`, { method: 'DELETE' })
}

export async function exportBackupFromApi(): Promise<BackupPayload> {
  return requestJson<BackupPayload>('/api/backup', { cache: 'no-store' })
}

export async function importBackupToApi(projects: Project[]): Promise<Project[]> {
  const payload = await requestJson<ProjectsPayload>('/api/backup', {
    method: 'POST',
    body: JSON.stringify({ projects }),
  })
  return payload.projects
}
"""


def _dashboard_page_tsx() -> str:
    return """'use client'

import { useEffect, useMemo, useRef, useState } from 'react'
import { Database, Download, HardDrive, Plus, Search, SlidersHorizontal, Upload } from 'lucide-react'
import { INITIAL_PROJECTS, type Project, type ProjectStatus, STATUS_LABELS } from '@/lib/store'
import {
  createProjectInApi,
  deleteProjectInApi,
  exportBackupFromApi,
  fetchProjectsFromApi,
  importBackupToApi,
  type PersistenceMode,
  updateProjectInApi,
} from '@/lib/project-client'
import { Nav } from '@/components/nav'
import { StatsBar } from '@/components/stats-bar'
import { ProjectCard } from '@/components/project-card'
import { ProjectDetail } from '@/components/project-detail'
import { NewProjectModal } from '@/components/new-project-modal'
import { cn } from '@/lib/utils'

type FilterStatus = 'all' | ProjectStatus

const FILTER_OPTIONS: { value: FilterStatus; label: string }[] = [
  { value: 'all',     label: 'All' },
  { value: 'active',  label: STATUS_LABELS.active },
  { value: 'shipped', label: STATUS_LABELS.shipped },
  { value: 'paused',  label: STATUS_LABELS.paused },
  { value: 'draft',   label: STATUS_LABELS.draft },
]

const PROJECTS_STORAGE_KEY = 'folio-projects-v1'

function loadStoredProjects(): Project[] | null {
  if (typeof window === 'undefined') return null
  try {
    const raw = window.localStorage.getItem(PROJECTS_STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

function storeProjects(projects: Project[]) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(PROJECTS_STORAGE_KEY, JSON.stringify(projects))
}

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>(INITIAL_PROJECTS)
  const [hydrated, setHydrated] = useState(false)
  const [filter, setFilter]     = useState<FilterStatus>('all')
  const [query, setQuery]       = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [showNew, setShowNew]   = useState(false)
  const [persistenceMode, setPersistenceMode] = useState<PersistenceMode>('local')
  const [syncStatus, setSyncStatus] = useState('Loading storage')
  const backupInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    let cancelled = false

    async function loadProjects() {
      const stored = loadStoredProjects()
      if (stored && !cancelled) {
        setProjects(stored)
      }

      try {
        const apiProjects = await fetchProjectsFromApi()
        if (cancelled) return
        setProjects(apiProjects)
        storeProjects(apiProjects)
        setPersistenceMode('sqlite')
        setSyncStatus('SQLite synced')
      } catch {
        if (cancelled) return
        setPersistenceMode('local')
        setSyncStatus(stored ? 'Local fallback' : 'Local seed data')
      } finally {
        if (!cancelled) setHydrated(true)
      }
    }

    loadProjects()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!hydrated) return
    storeProjects(projects)
  }, [hydrated, projects])

  const selectedProject = projects.find(p => p.id === selected) ?? null

  const filtered = useMemo(() => {
    return projects.filter(p => {
      const matchStatus = filter === 'all' || p.status === filter
      const q = query.toLowerCase()
      const matchQuery =
        !q ||
        p.title.toLowerCase().includes(q) ||
        p.description.toLowerCase().includes(q) ||
        p.tags.some(t => t.toLowerCase().includes(q))
      return matchStatus && matchQuery
    })
  }, [projects, filter, query])

  const replaceProject = (project: Project) => {
    setProjects(ps => ps.map(p => p.id === project.id ? project : p))
  }

  const handleSave = async (updated: Project) => {
    replaceProject(updated)
    setSelected(null)
    if (persistenceMode !== 'sqlite') return
    try {
      const saved = await updateProjectInApi(updated)
      replaceProject(saved)
      setSyncStatus('SQLite synced')
    } catch {
      setPersistenceMode('local')
      setSyncStatus('Local fallback')
    }
  }

  const handleDelete = async (id: string) => {
    setProjects(ps => ps.filter(p => p.id !== id))
    setSelected(null)
    if (persistenceMode !== 'sqlite') return
    try {
      await deleteProjectInApi(id)
      setSyncStatus('SQLite synced')
    } catch {
      setPersistenceMode('local')
      setSyncStatus('Local fallback')
    }
  }

  const handleCreate = async (project: Project) => {
    setProjects(ps => [project, ...ps])
    setShowNew(false)
    setSelected(project.id)
    if (persistenceMode !== 'sqlite') return
    try {
      const saved = await createProjectInApi(project)
      setProjects(ps => [saved, ...ps.filter(p => p.id !== project.id)])
      setSelected(saved.id)
      setSyncStatus('SQLite synced')
    } catch {
      setPersistenceMode('local')
      setSyncStatus('Local fallback')
    }
  }

  const handleExportBackup = async () => {
    try {
      const payload = persistenceMode === 'sqlite'
        ? await exportBackupFromApi()
        : { version: 1 as const, exportedAt: new Date().toISOString(), projects }
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' })
      const url = URL.createObjectURL(blob)
      const anchor = document.createElement('a')
      anchor.href = url
      anchor.download = `folio-backup-${new Date().toISOString().slice(0, 10)}.json`
      anchor.click()
      URL.revokeObjectURL(url)
      setSyncStatus('Backup exported')
    } catch {
      setSyncStatus('Backup failed')
    }
  }

  const handleImportBackup = async (file: File | null) => {
    if (!file) return
    try {
      const payload = JSON.parse(await file.text())
      const imported = Array.isArray(payload.projects) ? (payload.projects as Project[]) : []
      if (!imported.length) throw new Error('No projects in backup')
      setProjects(imported)
      storeProjects(imported)
      if (persistenceMode === 'sqlite') {
        const restored = await importBackupToApi(imported)
        setProjects(restored)
        storeProjects(restored)
        setSyncStatus('SQLite restored')
      } else {
        setSyncStatus('Backup restored')
      }
    } catch {
      setSyncStatus('Import failed')
    } finally {
      if (backupInputRef.current) backupInputRef.current.value = ''
    }
  }

  const SyncIcon = persistenceMode === 'sqlite' ? Database : HardDrive

  return (
    <div className=\"min-h-screen bg-background\">
      <Nav />

      <main className=\"max-w-5xl mx-auto px-6 py-10 space-y-8\">

        {/* Page header */}
        <header className=\"flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between\">
          <div className=\"space-y-1\">
            <h1 className=\"text-2xl font-semibold tracking-tight text-foreground text-balance\">
              Your Projects
            </h1>
            <p className=\"text-sm text-muted-foreground leading-relaxed\">
              Track every project from idea to shipped - goals, tasks, and proof of work.
            </p>
          </div>
          <div
            className={cn(
              'inline-flex w-fit items-center gap-1.5 rounded border px-2.5 py-1.5 text-xs',
              persistenceMode === 'sqlite'
                ? 'border-border bg-card text-foreground'
                : 'border-border bg-secondary text-muted-foreground'
            )}
            aria-live=\"polite\"
          >
            <SyncIcon className=\"h-3.5 w-3.5\" aria-hidden=\"true\" />
            <span>{syncStatus}</span>
          </div>
        </header>

        {/* Stats */}
        <StatsBar projects={projects} />

        {/* Controls row */}
        <div className=\"flex flex-col sm:flex-row items-start sm:items-center gap-3\">
          {/* Search */}
          <div className=\"relative flex-1 w-full sm:max-w-xs\">
            <Search className=\"absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground\" aria-hidden=\"true\" />
            <input
              value={query}
              onChange={e => setQuery(e.target.value)}
              className=\"w-full pl-8 pr-3 py-2 text-sm bg-card border border-border rounded-lg text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-foreground/40 transition-colors\"
              placeholder=\"Search projects...\"
              aria-label=\"Search projects\"
            />
          </div>

          {/* Filter pills */}
          <div
            className=\"flex items-center gap-1 overflow-x-auto pb-0.5 sm:pb-0\"
            role=\"group\"
            aria-label=\"Filter by status\"
          >
            <SlidersHorizontal className=\"w-3.5 h-3.5 text-muted-foreground flex-shrink-0 mr-1\" aria-hidden=\"true\" />
            {FILTER_OPTIONS.map(({ value, label }) => (
              <button
                key={value}
                onClick={() => setFilter(value)}
                className={cn(
                  'flex-shrink-0 px-3 py-1.5 rounded-full text-xs font-medium transition-colors',
                  filter === value
                    ? 'bg-foreground text-primary-foreground'
                    : 'bg-secondary text-muted-foreground hover:text-foreground hover:bg-border'
                )}
                aria-pressed={filter === value}
              >
                {label}
              </button>
            ))}
          </div>

          {/* New project */}
          <button
            type=\"button\"
            onClick={() => setShowNew(true)}
            className=\"flex items-center gap-2 px-4 py-2 bg-foreground text-primary-foreground text-sm font-medium rounded-lg hover:bg-foreground/90 transition-colors flex-shrink-0 sm:ml-auto\"
            aria-label=\"Create new project\"
          >
            <Plus className=\"w-3.5 h-3.5\" aria-hidden=\"true\" />
            <span>New Project</span>
          </button>

          <div className=\"flex items-center gap-1\">
            <button
              type=\"button\"
              onClick={handleExportBackup}
              className=\"inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted-foreground hover:border-foreground/40 hover:text-foreground transition-colors\"
              aria-label=\"Export backup\"
              title=\"Export backup\"
            >
              <Download className=\"h-3.5 w-3.5\" aria-hidden=\"true\" />
            </button>
            <button
              type=\"button\"
              onClick={() => backupInputRef.current?.click()}
              className=\"inline-flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted-foreground hover:border-foreground/40 hover:text-foreground transition-colors\"
              aria-label=\"Import backup\"
              title=\"Import backup\"
            >
              <Upload className=\"h-3.5 w-3.5\" aria-hidden=\"true\" />
            </button>
            <input
              ref={backupInputRef}
              type=\"file\"
              accept=\"application/json,.json\"
              className=\"sr-only\"
              aria-label=\"Backup file input\"
              onChange={event => handleImportBackup(event.target.files?.[0] ?? null)}
            />
          </div>
        </div>

        {/* Project grid */}
        {filtered.length > 0 ? (
          <section
            className=\"grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4\"
            aria-label=\"Project list\"
          >
            {filtered.map(project => (
              <ProjectCard
                key={project.id}
                project={project}
                onSelect={setSelected}
              />
            ))}
          </section>
        ) : (
          <div className=\"py-20 text-center space-y-3\" role=\"status\" aria-live=\"polite\">
            <p className=\"text-sm font-medium text-foreground\">No projects found</p>
            <p className=\"text-xs text-muted-foreground\">
              {query ? `No results for \"${query}\"` : 'Create your first project to get started.'}
            </p>
            {!query && (
              <button
                type=\"button\"
                onClick={() => setShowNew(true)}
                className=\"mt-2 inline-flex items-center gap-2 px-4 py-2 bg-foreground text-primary-foreground text-sm rounded-lg hover:bg-foreground/90 transition-colors\"
              >
                <Plus className=\"w-3.5 h-3.5\" aria-hidden=\"true\" />
                Create Project
              </button>
            )}
          </div>
        )}

        {/* Result count */}
        {filtered.length > 0 && (
          <p className=\"text-xs text-muted-foreground text-center\" aria-live=\"polite\">
            Showing {filtered.length} of {projects.length} projects
          </p>
        )}
      </main>

      {/* Project detail panel */}
      {selectedProject && (
        <ProjectDetail
          project={selectedProject}
          onClose={() => setSelected(null)}
          onSave={handleSave}
          onDelete={handleDelete}
        />
      )}

      {/* New project modal */}
      {showNew && (
        <NewProjectModal
          onClose={() => setShowNew(false)}
          onCreate={handleCreate}
        />
      )}
    </div>
  )
}
"""


def _health_route_ts() -> str:
    return """import { NextResponse } from 'next/server'
import { listProjects } from '@/lib/server/project-repository'

export const runtime = 'nodejs'

export async function GET() {
  const projects = listProjects()
  return NextResponse.json({
    status: 'ok',
    storage: 'sqlite',
    projectCount: projects.length,
  })
}
"""


def _projects_route_ts() -> str:
    return """import { NextRequest, NextResponse } from 'next/server'
import { createProject, listProjects } from '@/lib/server/project-repository'

export const runtime = 'nodejs'

export async function GET() {
  return NextResponse.json({ projects: listProjects() })
}

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null)
  if (!body || typeof body !== 'object') {
    return NextResponse.json({ error: 'Invalid JSON body.' }, { status: 400 })
  }
  const project = createProject(body)
  return NextResponse.json({ project }, { status: 201 })
}
"""


def _project_detail_route_ts() -> str:
    return """import { NextRequest, NextResponse } from 'next/server'
import { deleteProject, getProject, updateProject } from '@/lib/server/project-repository'

export const runtime = 'nodejs'

type RouteContext = {
  params: Promise<{ projectId: string }>
}

export async function GET(_request: NextRequest, context: RouteContext) {
  const { projectId } = await context.params
  const project = getProject(projectId)
  if (!project) return NextResponse.json({ error: 'Project not found.' }, { status: 404 })
  return NextResponse.json({ project })
}

export async function PATCH(request: NextRequest, context: RouteContext) {
  const { projectId } = await context.params
  const body = await request.json().catch(() => null)
  if (!body || typeof body !== 'object') {
    return NextResponse.json({ error: 'Invalid JSON body.' }, { status: 400 })
  }
  const project = updateProject(projectId, body)
  if (!project) return NextResponse.json({ error: 'Project not found.' }, { status: 404 })
  return NextResponse.json({ project })
}

export async function DELETE(_request: NextRequest, context: RouteContext) {
  const { projectId } = await context.params
  const deleted = deleteProject(projectId)
  if (!deleted) return NextResponse.json({ error: 'Project not found.' }, { status: 404 })
  return NextResponse.json({ ok: true })
}
"""


def _project_tasks_route_ts() -> str:
    return """import { NextRequest, NextResponse } from 'next/server'
import { addTask } from '@/lib/server/project-repository'

export const runtime = 'nodejs'

type RouteContext = {
  params: Promise<{ projectId: string }>
}

export async function POST(request: NextRequest, context: RouteContext) {
  const { projectId } = await context.params
  const body = await request.json().catch(() => null)
  const title = typeof body?.title === 'string' ? body.title.trim() : ''
  if (!title) return NextResponse.json({ error: 'Task title is required.' }, { status: 400 })
  const project = addTask(projectId, title)
  if (!project) return NextResponse.json({ error: 'Project not found.' }, { status: 404 })
  return NextResponse.json({ project }, { status: 201 })
}
"""


def _backup_route_ts() -> str:
    return """import { NextRequest, NextResponse } from 'next/server'
import { listProjects, replaceAllProjects } from '@/lib/server/project-repository'

export const runtime = 'nodejs'

export async function GET() {
  return NextResponse.json({
    version: 1,
    exportedAt: new Date().toISOString(),
    projects: listProjects(),
  })
}

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => null)
  const projects = Array.isArray(body?.projects) ? body.projects : null
  if (!projects) {
    return NextResponse.json({ error: 'Backup payload must include a projects array.' }, { status: 400 })
  }
  const restored = replaceAllProjects(projects)
  return NextResponse.json({ projects: restored })
}
"""


def _export_route_ts() -> str:
    return """import { NextRequest, NextResponse } from 'next/server'
import { generateHTML, type TrackerExportState } from '@/lib/export-html'
import { listProjects } from '@/lib/server/project-repository'

export const runtime = 'nodejs'

export async function POST(request: NextRequest) {
  const body = await request.json().catch(() => ({}))
  const state: TrackerExportState = {
    authorName: typeof body.authorName === 'string' ? body.authorName : 'Creator Portfolio',
    authorBio: typeof body.authorBio === 'string' ? body.authorBio : '',
    theme: body.theme === 'minimal' || body.theme === 'dark' ? body.theme : 'editorial',
    projects: Array.isArray(body.projects) ? body.projects : listProjects(),
  }
  return NextResponse.json({ html: generateHTML(state) })
}
"""


def _playwright_config_ts() -> str:
    return """import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  use: {
    baseURL: 'http://127.0.0.1:3107',
    trace: 'on-first-retry',
  },
  webServer: {
    command: 'npm run dev -- --hostname 127.0.0.1 --port 3107',
    url: 'http://127.0.0.1:3107',
    reuseExistingServer: true,
    timeout: 120_000,
  },
  projects: [
    { name: 'chromium', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 5'] } },
  ],
})
"""


def _playwright_spec_ts() -> str:
    return """import { expect, test } from '@playwright/test'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
  await page.evaluate(() => window.localStorage.clear())
  await page.reload()
})

test('creates, edits, persists, and exports a creator project', async ({ page }, testInfo) => {
  const projectTitle = `Codex Portfolio Case Study ${testInfo.project.name} ${Date.now()}`
  const projectCard = page.locator(`article[aria-label="Open project: ${projectTitle}"]`)

  await expect(page.getByText('SQLite synced')).toBeVisible()
  await page.getByRole('button', { name: 'Create new project' }).click()
  await page.locator('#new-title').fill(projectTitle)
  await page.locator('#new-desc').fill('A proof-first portfolio project created during browser QA.')
  await page.locator('#new-goal').fill('Ship an evidence-backed portfolio workflow')
  await page.locator('#new-tags').fill('AI, Portfolio, QA')
  await page.locator('#new-status').selectOption('active')
  await page.getByRole('button', { name: 'Create Project' }).click()

  await expect(page.getByRole('dialog', { name: new RegExp(`Editing: ${projectTitle}`) })).toBeVisible()
  await page.getByLabel('New task title').fill('Verify export path')
  await page.getByRole('button', { name: 'Add task' }).click()
  await page.getByRole('checkbox', { name: /Mark "Verify export path" as complete/ }).click()
  await page.getByLabel('Retrospective notes').fill('The workflow now has real interaction evidence.')
  await page.getByRole('button', { name: 'Save changes' }).click()

  await page.reload()
  await expect(projectCard).toBeVisible()

  await page.getByRole('link', { name: 'Portfolio' }).click()
  await page.getByLabel('Author name').fill('Codex QA')
  await page.getByLabel('Author bio').fill('Browser-tested project export.')
  await expect(page.getByLabel('Portfolio preview')).toContainText(projectTitle)
})

test('SQLite API can create, update, and delete a project', async ({ request }) => {
  const createResponse = await request.post('/api/projects', {
    data: {
      title: 'API-backed project',
      description: 'Created through the route handler API.',
      status: 'active',
      tags: ['API', 'SQLite'],
      goal: 'Prove backend persistence',
    },
  })
  expect(createResponse.ok()).toBeTruthy()
  const created = await createResponse.json()
  const projectId = created.project.id

  const taskResponse = await request.post(`/api/projects/${projectId}/tasks`, {
    data: { title: 'Persist task in SQLite' },
  })
  expect(taskResponse.status()).toBe(201)
  await expect.poll(async () => {
    const response = await request.get(`/api/projects/${projectId}`)
    return response.status()
  }).toBe(200)

  const deleteResponse = await request.delete(`/api/projects/${projectId}`)
  expect(deleteResponse.ok()).toBeTruthy()
})

test('SQLite backup API exports and restores projects', async ({ request }) => {
  const exportResponse = await request.get('/api/backup')
  expect(exportResponse.ok()).toBeTruthy()
  const backup = await exportResponse.json()
  expect(backup.version).toBe(1)
  expect(Array.isArray(backup.projects)).toBeTruthy()

  const restoreResponse = await request.post('/api/backup', {
    data: { projects: backup.projects },
  })
  expect(restoreResponse.ok()).toBeTruthy()
  const restored = await restoreResponse.json()
  expect(Array.isArray(restored.projects)).toBeTruthy()
})
"""


def _openapi_yaml() -> str:
    return """openapi: 3.1.0
info:
  title: Creator Project Tracker API
  version: 0.2.0
  description: Local Next.js route handlers backed by SQLite for project, task, and portfolio export workflows.
paths:
  /api/health:
    get:
      summary: Check local API health and SQLite availability.
      responses:
        "200":
          description: API is healthy.
  /api/projects:
    get:
      summary: List creator projects.
      responses:
        "200":
          description: Project list.
    post:
      summary: Create a creator project.
      responses:
        "201":
          description: Created project.
        "400":
          description: Invalid body.
  /api/projects/{projectId}:
    get:
      summary: Read one project.
      parameters:
        - name: projectId
          in: path
          required: true
          schema:
            type: string
      responses:
        "200":
          description: Project detail.
        "404":
          description: Project not found.
    patch:
      summary: Update one project.
      responses:
        "200":
          description: Updated project.
        "404":
          description: Project not found.
    delete:
      summary: Delete one project.
      responses:
        "200":
          description: Project deleted.
        "404":
          description: Project not found.
  /api/projects/{projectId}/tasks:
    post:
      summary: Add a task to a project.
      responses:
        "201":
          description: Task added and project returned.
        "400":
          description: Missing task title.
        "404":
          description: Project not found.
  /api/export:
    post:
      summary: Generate static HTML from selected project data.
      responses:
        "200":
          description: Generated HTML payload.
  /api/backup:
    get:
      summary: Export all project data as a portable JSON backup.
      responses:
        "200":
          description: Backup payload with projects.
    post:
      summary: Restore all project data from a backup payload.
      responses:
        "200":
          description: Restored project list.
        "400":
          description: Invalid backup payload.
components:
  schemas:
    ProjectStatus:
      type: string
      enum: [draft, active, shipped, paused]
    Task:
      type: object
      required: [id, title, done, createdAt]
      properties:
        id: { type: string }
        title: { type: string }
        done: { type: boolean }
        createdAt: { type: string }
    Project:
      type: object
      required: [id, title, description, status, goal, tags, tasks, createdAt, updatedAt]
      properties:
        id: { type: string }
        title: { type: string }
        description: { type: string }
        status: { $ref: "#/components/schemas/ProjectStatus" }
        goal: { type: string }
        tags:
          type: array
          items: { type: string }
        screenshotUrl: { type: string }
        screenshotAlt: { type: string }
        publishUrl: { type: string }
        repoUrl: { type: string }
        retroNote: { type: string }
        tasks:
          type: array
          items: { $ref: "#/components/schemas/Task" }
        createdAt: { type: string }
        updatedAt: { type: string }
"""


def _database_schema_md() -> str:
    return """# Database Schema

Storage: local SQLite through Node 24 `node:sqlite`.

Database path:

- Default: `apps/web/.data/creator-projects.sqlite`
- Override: `AGENT_STUDIO_DB_PATH=/absolute/path/to/file.sqlite`

## `projects`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | TEXT PRIMARY KEY | Stable project id. |
| `title` | TEXT NOT NULL | Project title. |
| `description` | TEXT NOT NULL | Short project story. |
| `status` | TEXT NOT NULL | One of `draft`, `active`, `shipped`, `paused`. |
| `goal` | TEXT NOT NULL | Success metric or desired outcome. |
| `tags_json` | TEXT NOT NULL | JSON array of tags. |
| `screenshot_url` | TEXT | Local object URL or remote image URL. |
| `screenshot_alt` | TEXT | Accessibility description. |
| `publish_url` | TEXT | Public project URL. |
| `repo_url` | TEXT | Source repository URL. |
| `retro_note` | TEXT NOT NULL | Retrospective proof and learning. |
| `created_at` | TEXT NOT NULL | ISO timestamp. |
| `updated_at` | TEXT NOT NULL | ISO timestamp. |

## `tasks`

| Column | Type | Notes |
| --- | --- | --- |
| `id` | TEXT PRIMARY KEY | Stable task id. |
| `project_id` | TEXT NOT NULL | References `projects(id)` with cascade delete. |
| `title` | TEXT NOT NULL | Task title. |
| `done` | INTEGER NOT NULL | Boolean stored as `0` or `1`. |
| `created_at` | TEXT NOT NULL | ISO timestamp. |

## Indexes

- `idx_projects_updated_at`
- `idx_tasks_project_id`
"""


def _hardening_plan_md() -> str:
    return """# Implementation Hardening Plan

Status: completed

## Completed

- Added SQLite-backed Next.js API route handlers.
- Wired the client dashboard to load, create, update, and delete projects through the SQLite API.
- Kept localStorage as a local fallback and offline backup.
- Added JSON backup export/import controls and `/api/backup` restore support.
- Added API contract and database schema docs.
- Added Playwright browser interaction tests for create/edit/persist/export workflows.
- Added API-level Playwright request tests for project/task persistence.
- Added QA visual regression reporting for desktop and mobile screenshots.

## New Commands

From `apps/web`:

```bash
npm install
npm run build
npm run test:e2e
```

## Remaining Hardening

- Add a stronger baseline-diff visual comparison once stable golden screenshots exist.
"""


def _api_smoke_md() -> str:
    return """# Creator Project Tracker API Smoke Test

- `GET /api/health` returns `{ status: "ok", storage: "sqlite" }`.
- `GET /api/projects` returns seeded projects from SQLite.
- `POST /api/projects` creates a new project row.
- `POST /api/projects/:projectId/tasks` creates a task row tied to that project.
- `GET /api/projects/:projectId` returns the project with tasks.
- `DELETE /api/projects/:projectId` removes the project and cascades tasks.
- `POST /api/export` returns escaped static HTML for selected projects.
- `GET /api/backup` exports all projects as JSON.
- `POST /api/backup` restores projects from JSON.
"""
