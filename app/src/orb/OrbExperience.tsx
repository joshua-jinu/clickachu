import { Orb } from './Orb'

/** The orb window hosts only the orb; clicking it opens the shared QuickBar window beside it. */
export function OrbExperience() {
  return (
    <div className="relative h-full w-full">
      <Orb />
    </div>
  )
}
