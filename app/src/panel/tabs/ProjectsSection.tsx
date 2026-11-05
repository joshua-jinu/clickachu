/** Settings → Projects (PLAN §15): pick a folder, review detected commands, save the profile. */
import { type FormEvent, useCallback, useEffect, useState } from 'react'
import { api } from '@shared/api'
import type { AllowedRoot, Project, ProjectCommand } from '@shared/protocol'
import { useStore } from '@shared/store'
import { buttonClass, errorText, ghostButtonClass, inputClass } from '../ui'

interface Draft {
  /** Set when editing a saved profile. */
  id?: number
  name: string
  path: string
  commands: ProjectCommand[]
}

const normPath = (p: string): string => p.replace(/\//g, '\\').replace(/\\+$/, '').toLowerCase()

/** Keeps the folder inside the allowed roots, offering to add or upgrade it (writable). */
async function offerAllowedRoot(path: string, onChanged: () => void): Promise<void> {
  const roots: AllowedRoot[] = await api.listRoots()
  const target = normPath(path)
  const exact = roots.find((r) => normPath(r.path) === target)
  if (exact?.can_write) return
  if (exact) {
    if (window.confirm(`Allow Clickachu to write to ${exact.path}?`)) {
      await api.updateRoot(exact.id, { can_write: true })
      onChanged()
    }
    return
  }
  const covering = roots.find((r) => target.startsWith(`${normPath(r.path)}\\`) && r.can_write)
  if (covering) return
  if (window.confirm(`Add ${path} to your allowed folders, with write access?`)) {
    await api.addRoot({ path, can_write: true })
    onChanged()
  }
}

function CommandEditor({
  command,
  onChange,
  onRemove,
}: {
  command: ProjectCommand
  onChange: (c: ProjectCommand) => void
  onRemove: () => void
}) {
  return (
    <li className="space-y-1 rounded-md border border-ck-border bg-black/20 p-2">
      <div className="flex gap-1.5">
        <input
          value={command.name}
          onChange={(e) => onChange({ ...command, name: e.target.value })}
          placeholder="name"
          aria-label="Command name"
          className={`${inputClass} w-24 shrink-0`}
        />
        <input
          value={command.cmd}
          onChange={(e) => onChange({ ...command, cmd: e.target.value })}
          placeholder="command"
          aria-label="Command"
          spellCheck={false}
          className={`${inputClass} font-mono`}
        />
      </div>
      <div className="flex gap-1.5">
        <input
          value={command.cwd}
          onChange={(e) => onChange({ ...command, cwd: e.target.value })}
          placeholder="working folder"
          aria-label="Working folder"
          spellCheck={false}
          className={`${inputClass} font-mono text-xs`}
        />
        <input
          value={command.port ?? ''}
          onChange={(e) => {
            const n = Number.parseInt(e.target.value, 10)
            onChange({ ...command, port: Number.isNaN(n) ? null : n })
          }}
          inputMode="numeric"
          placeholder="port"
          aria-label="Port"
          className={`${inputClass} w-16 shrink-0`}
        />
        <button
          type="button"
          onClick={onRemove}
          aria-label="Remove command"
          className="shrink-0 px-1 text-ck-muted hover:text-red-300"
        >
          ✕
        </button>
      </div>
    </li>
  )
}

function ProjectEditor({
  draft,
  onChange,
  onSave,
  onCancel,
  busy,
}: {
  draft: Draft
  onChange: (d: Draft) => void
  onSave: () => void
  onCancel: () => void
  busy: boolean
}) {
  function submit(e: FormEvent) {
    e.preventDefault()
    onSave()
  }
  return (
    <form onSubmit={submit} className="space-y-2 rounded-md border border-ck-violet/40 p-2">
      <label className="block text-xs text-ck-muted">
        Name
        <input
          value={draft.name}
          onChange={(e) => onChange({ ...draft, name: e.target.value })}
          className={inputClass}
        />
      </label>
      <p className="text-xs break-all text-ck-muted">{draft.path}</p>
      {draft.commands.length === 0 && (
        <p className="text-xs text-ck-muted">No commands detected — add the ones you use.</p>
      )}
      <ul className="space-y-1.5">
        {draft.commands.map((c, i) => (
          <CommandEditor
            key={i}
            command={c}
            onChange={(next) =>
              onChange({ ...draft, commands: draft.commands.map((x, j) => (j === i ? next : x)) })
            }
            onRemove={() =>
              onChange({ ...draft, commands: draft.commands.filter((_, j) => j !== i) })
            }
          />
        ))}
      </ul>
      <button
        type="button"
        className={ghostButtonClass}
        onClick={() =>
          onChange({
            ...draft,
            commands: [
              ...draft.commands,
              { name: '', cmd: '', cwd: draft.path, port: null, trusted: false },
            ],
          })
        }
      >
        + Add command
      </button>
      <div className="flex gap-2">
        <button type="submit" className={buttonClass} disabled={busy || !draft.name.trim()}>
          Save project
        </button>
        <button type="button" className={ghostButtonClass} onClick={onCancel}>
          Cancel
        </button>
      </div>
    </form>
  )
}

export function ProjectsSection({ onRootsChanged }: { onRootsChanged: () => void }) {
  const connection = useStore((s) => s.connection)
  const [projects, setProjects] = useState<Project[]>([])
  const [draft, setDraft] = useState<Draft | null>(null)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    api
      .listProjects()
      .then((rows) => { setProjects(rows); setLoaded(true) })
      .catch((err: unknown) => { setNote(errorText(err)); setLoaded(true) })
  }, [])

  useEffect(() => {
    if (connection === 'connected') load()
  }, [connection, load])

  async function addProject() {
    setNote('')
    const folder = await window.clickachu.pickFolder()
    if (!folder) return
    try {
      const proposal = await api.detectProject(folder)
      setDraft({ name: proposal.name, path: proposal.path, commands: proposal.commands })
    } catch (err) {
      setNote(errorText(err))
    }
  }

  async function save() {
    if (!draft) return
    setBusy(true)
    try {
      const commands = draft.commands.filter((c) => c.name.trim() && c.cmd.trim())
      const body = { name: draft.name.trim(), path: draft.path, commands }
      if (draft.id === undefined) {
        await api.createProject(body)
        setDraft(null)
        load()
        setNote(`Saved ${body.name}.`)
        try {
          await offerAllowedRoot(draft.path, onRootsChanged)
        } catch (err) {
          setNote(`Saved, but the folder wasn't added: ${errorText(err)}`)
        }
      } else {
        await api.updateProject(draft.id, body)
        setDraft(null)
        load()
        setNote(`Updated ${body.name}.`)
      }
    } catch (err) {
      setNote(errorText(err))
    } finally {
      setBusy(false)
    }
  }

  async function remove(project: Project) {
    if (!window.confirm(`Delete the project profile “${project.name}”?`)) return
    try {
      await api.deleteProject(project.id)
      load()
    } catch (err) {
      setNote(errorText(err))
    }
  }

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Projects</h2>
        <button type="button" className={ghostButtonClass} onClick={() => void addProject()}>
          Add project…
        </button>
      </div>
      {projects.length === 0 && !draft && (
        <p className={loaded ? 'ck-empty-state ck-empty-inline' : 'ck-inline-state'}>
          {!loaded
            ? 'Loading project profiles…'
            : 'Add a folder and Clickachu proposes its dev commands (package.json, compose, Makefile…).'}
        </p>
      )}
      <ul className="space-y-1.5">
        {projects.map((p) => (
          <li key={p.id} className="rounded-md border border-ck-border bg-black/20 p-2 text-sm">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium">{p.name}</span>
              <span className="flex gap-2 text-xs text-ck-muted">
                <button
                  type="button"
                  className="hover:text-ck-text"
                  onClick={() =>
                    setDraft({ id: p.id, name: p.name, path: p.path, commands: p.commands })
                  }
                >
                  Edit
                </button>
                <button type="button" className="hover:text-red-300" onClick={() => void remove(p)}>
                  Delete
                </button>
              </span>
            </div>
            <p className="text-xs break-all text-ck-muted">
              {p.path} · {p.commands.length} command{p.commands.length === 1 ? '' : 's'}
            </p>
          </li>
        ))}
      </ul>
      {draft && (
        <ProjectEditor
          draft={draft}
          onChange={setDraft}
          onSave={() => void save()}
          onCancel={() => setDraft(null)}
          busy={busy}
        />
      )}
      {note && <p className="text-xs text-ck-muted">{note}</p>}
    </section>
  )
}
