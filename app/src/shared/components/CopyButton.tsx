import { useEffect, useState } from 'react'

interface CopyButtonProps {
  getText: () => string
  label?: string
  className?: string
}

export function CopyButton({ getText, label = 'Copy', className = '' }: CopyButtonProps) {
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle')

  useEffect(() => {
    if (state === 'idle') return
    const t = setTimeout(() => setState('idle'), 1500)
    return () => clearTimeout(t)
  }, [state])

  function copy() {
    navigator.clipboard.writeText(getText()).then(
      () => setState('copied'),
      (err: unknown) => {
        console.warn('[copy] clipboard write failed', err)
        setState('failed')
      },
    )
  }

  return (
    <button
      type="button"
      onClick={copy}
      className={`rounded-md border border-ck-border bg-black/40 px-2 py-0.5 text-xs text-ck-text outline-none hover:border-ck-violet/60 focus-visible:border-ck-violet ${className}`}
    >
      {state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : label}
    </button>
  )
}
