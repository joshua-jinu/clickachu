/** Shared Tailwind class strings for the panel's tabs. */
export const inputClass =
  'w-full rounded-xl border border-white/[0.08] bg-white/[0.04] px-3 py-1.5 text-sm text-ck-text outline-none transition-colors focus:border-ck-violet/60 focus:bg-white/[0.06]'
export const buttonClass =
  'rounded-xl bg-ck-violet px-3.5 py-1.5 text-xs font-medium text-white transition-all hover:bg-ck-violet/90 disabled:opacity-40'
export const ghostButtonClass =
  'rounded-xl border border-white/[0.08] bg-white/[0.03] px-2.5 py-1 text-xs text-ck-muted transition-colors hover:border-white/15 hover:text-ck-text disabled:opacity-40'

export function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err)
}
