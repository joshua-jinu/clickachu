/** Settings → Allowed folders (PLAN §13.2): where the agent may read, and where it may write. */
import { useCallback, useEffect, useState } from 'react'
import { api } from '@shared/api'
import type { AllowedRoot } from '@shared/protocol'
import { useStore } from '@shared/store'
import { errorText, ghostButtonClass } from '../ui'

export function RootsSection({ revision }: { revision: number }) {
  const connection = useStore((s) => s.connection)
  const [roots, setRoots] = useState<AllowedRoot[]>([])
  const [note, setNote] = useState('')
  const [loaded, setLoaded] = useState(false)

  const load = useCallback(() => {
    api
      .listRoots()
      .then((rows) => { setRoots(rows); setLoaded(true) })
      .catch((err: unknown) => { setNote(errorText(err)); setLoaded(true) })
  }, [])

  useEffect(() => {
    if (connection === 'connected') load()
  }, [connection, load, revision])

  async function run(action: () => Promise<unknown>) {
    try {
      await action()
      setNote('')
      load()
    } catch (err) {
      setNote(errorText(err))
    }
  }

  async function add() {
    const folder = await window.clickachu.pickFolder()
    if (folder) await run(() => api.addRoot({ path: folder, can_write: true }))
  }

  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium">Allowed folders</h2>
        <button type="button" className={ghostButtonClass} onClick={() => void add()}>
          Add folder…
        </button>
      </div>
      {roots.length === 0 && (
        <p className={loaded ? 'ck-empty-state ck-empty-inline' : 'ck-inline-state'}>
          {loaded ? "The agent can't read or write files anywhere until you add a folder." : 'Loading allowed folders…'}
        </p>
      )}
      <ul className="space-y-1.5">
        {roots.map((r) => (
          <li key={r.id} className="rounded-md border border-ck-border bg-black/20 p-2 text-xs">
            <p className="break-all">{r.path}</p>
            <div className="mt-1 flex items-center justify-between text-ck-muted">
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  checked={r.can_write}
                  onChange={(e) =>
                    void run(() => api.updateRoot(r.id, { can_write: e.target.checked }))
                  }
                />
                can write
              </label>
              <button
                type="button"
                className="hover:text-red-300"
                onClick={() => void run(() => api.removeRoot(r.id))}
              >
                Remove
              </button>
            </div>
          </li>
        ))}
      </ul>
      {note && <p className="text-xs text-red-300">{note}</p>}
    </section>
  )
}
