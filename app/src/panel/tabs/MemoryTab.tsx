/** Memory tab (PLAN §9.3, §14): grouped by scope, search, inline edit, pin, enable, delete. */
import { type ChangeEvent, type FormEvent, useCallback, useEffect, useRef, useState } from 'react'
import { api } from '@shared/api'
import type { Memory, MemoryExport } from '@shared/protocol'
import { useStore } from '@shared/store'
import { buttonClass, errorText, ghostButtonClass, inputClass } from '../ui'

const SEARCH_DEBOUNCE_MS = 200

interface Group {
  key: string
  title: string
  rank: number
  items: Memory[]
}

function groupByScope(memories: Memory[]): Group[] {
  const groups = new Map<string, Group>()
  for (const m of memories) {
    const key = `${m.scope_type}:${m.scope_key.toLowerCase()}`
    let group = groups.get(key)
    if (!group) {
      const rank = { global: 0, project: 1, app: 2 }[m.scope_type]
      const title =
        m.scope_type === 'global'
          ? 'Global'
          : `${m.scope_type === 'project' ? 'Project' : 'App'}: ${m.scope_key}`
      groups.set(key, (group = { key, title, rank, items: [] }))
    }
    group.items.push(m)
  }
  return [...groups.values()].sort((a, b) => a.rank - b.rank || a.title.localeCompare(b.title))
}

function MemoryRow({ memory, onChanged }: { memory: Memory; onChanged: () => void }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(memory.content)
  const [error, setError] = useState('')

  async function run(action: () => Promise<unknown>) {
    try {
      await action()
      setError('')
      onChanged()
      return true
    } catch (err) {
      setError(errorText(err))
      return false
    }
  }

  async function save(e: FormEvent) {
    e.preventDefault()
    const content = draft.trim()
    if (!content) return
    if (await run(() => api.updateMemory(memory.id, { content }))) setEditing(false)
  }

  return (
    <li
      className={`rounded-md border border-ck-border bg-black/20 p-2 text-sm ${
        memory.enabled ? '' : 'opacity-50'
      }`}
    >
      {editing ? (
        <form onSubmit={save} className="space-y-2">
          <textarea
            autoFocus
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            rows={3}
            className={`${inputClass} resize-none`}
          />
          <div className="flex gap-2">
            <button type="submit" className={buttonClass}>
              Save
            </button>
            <button
              type="button"
              className={ghostButtonClass}
              onClick={() => {
                setEditing(false)
                setDraft(memory.content)
              }}
            >
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <p className="whitespace-pre-wrap">{memory.content}</p>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-ck-muted">
        <span>{memory.category}</span>
        {memory.use_count > 0 && <span>used {memory.use_count}×</span>}
        <label className="flex items-center gap-1">
          <input
            type="checkbox"
            checked={memory.enabled}
            onChange={(e) =>
              void run(() => api.updateMemory(memory.id, { enabled: e.target.checked }))
            }
          />
          on
        </label>
        <button
          type="button"
          className={memory.pinned ? 'text-ck-violet' : 'hover:text-ck-text'}
          onClick={() => void run(() => api.updateMemory(memory.id, { pinned: !memory.pinned }))}
        >
          {memory.pinned ? 'Pinned' : 'Pin'}
        </button>
        {!editing && (
          <button
            type="button"
            className="hover:text-ck-text"
            onClick={() => {
              setDraft(memory.content)
              setEditing(true)
            }}
          >
            Edit
          </button>
        )}
        <button
          type="button"
          className="ml-auto hover:text-red-300"
          onClick={() => void run(() => api.deleteMemory(memory.id))}
        >
          Delete
        </button>
      </div>
      {error && <p className="mt-1 text-xs text-red-300">{error}</p>}
    </li>
  )
}

function downloadJson(data: MemoryExport, filename: string): void {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }),
  )
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function parseImport(text: string): MemoryExport {
  const raw: unknown = JSON.parse(text)
  const memories = Array.isArray(raw) ? raw : (raw as { memories?: unknown } | null)?.memories
  if (!Array.isArray(memories)) throw new Error('Expected {"memories": [...]}')
  return { version: 1, memories: memories as MemoryExport['memories'] }
}

export function MemoryTab() {
  const connection = useStore((s) => s.connection)
  const savedSeq = useStore((s) => s.lastSaved?.seq)
  const [memories, setMemories] = useState<Memory[] | null>(null)
  const [memoryOn, setMemoryOn] = useState(true)
  const [query, setQuery] = useState('')
  const [adding, setAdding] = useState('')
  const [note, setNote] = useState('')
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(() => {
    api
      .listMemories({ q: query.trim() || undefined })
      .then((rows) => {
        setMemories(rows)
        setNote('')
      })
      .catch((err: unknown) => setNote(errorText(err)))
  }, [query])

  useEffect(() => {
    if (connection !== 'connected') return
    const timer = setTimeout(load, query ? SEARCH_DEBOUNCE_MS : 0)
    return () => clearTimeout(timer)
  }, [connection, load, query, savedSeq])

  useEffect(() => {
    if (connection !== 'connected') return
    api
      .getSettings()
      .then((s) => setMemoryOn(s.memory_enabled))
      .catch((err: unknown) => setNote(errorText(err)))
  }, [connection])

  function toggleMemory(on: boolean) {
    setMemoryOn(on)
    api.putSettings({ memory_enabled: on }).catch((err: unknown) => {
      setMemoryOn(!on)
      setNote(errorText(err))
    })
  }

  async function add(e: FormEvent) {
    e.preventDefault()
    const content = adding.trim()
    if (!content) return
    try {
      await api.createMemory({ content })
      setAdding('')
      load()
    } catch (err) {
      setNote(errorText(err))
    }
  }

  async function exportAll() {
    try {
      downloadJson(await api.exportMemories(), 'clickachu-memories.json')
    } catch (err) {
      setNote(errorText(err))
    }
  }

  async function importFile(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0]
    e.target.value = ''
    if (!file) return
    try {
      const result = await api.importMemories(parseImport(await file.text()))
      setNote(`Imported ${result.imported}, skipped ${result.skipped}.`)
      load()
    } catch (err) {
      setNote(errorText(err))
    }
  }

  const groups = groupByScope(memories ?? [])

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3">
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={memoryOn}
          onChange={(e) => toggleMemory(e.target.checked)}
        />
        Memory {memoryOn ? 'on' : 'off'}
      </label>
      {memoryOn && (
        <p className="text-xs text-ck-muted">
          Clear personal details and preferences you share are saved automatically.
          You can edit, disable or delete them here.
        </p>
      )}
      {!memoryOn && (
        <p className="text-xs text-amber-300">
          Memory is off: nothing is saved and no memories are sent to the model.
        </p>
      )}

      <input
        type="search"
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search memories…"
        className={inputClass}
      />

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto pr-1">
        {memories === null && <p className="ck-inline-state">Retrieving memory index…</p>}
        {memories?.length === 0 && (
          <p className="text-xs text-ck-muted">
            {query ? 'No matches.' : 'Nothing remembered yet. Tell Clickachu a detail such as “I prefer concise answers”.'}
          </p>
        )}
        {groups.map((g) => (
          <section key={g.key}>
            <h3 className="mb-1.5 text-xs font-semibold tracking-wide text-ck-muted uppercase">
              {g.title}
            </h3>
            <ul className="space-y-1.5">
              {g.items.map((m) => (
                <MemoryRow key={m.id} memory={m} onChanged={load} />
              ))}
            </ul>
          </section>
        ))}
      </div>

      <form onSubmit={add} className="flex gap-2">
        <input
          value={adding}
          onChange={(e) => setAdding(e.target.value)}
          placeholder="Add a memory…"
          disabled={!memoryOn}
          className={inputClass}
        />
        <button type="submit" className={buttonClass} disabled={!memoryOn || !adding.trim()}>
          Add
        </button>
      </form>

      <div className="flex items-center gap-2">
        <button type="button" className={ghostButtonClass} onClick={() => void exportAll()}>
          Export
        </button>
        <button
          type="button"
          className={ghostButtonClass}
          disabled={!memoryOn}
          onClick={() => fileRef.current?.click()}
        >
          Import
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="application/json,.json"
          className="hidden"
          onChange={(e) => void importFile(e)}
        />
        <span className="min-w-0 truncate text-xs text-ck-muted" title={note}>
          {note}
        </span>
      </div>
    </div>
  )
}
