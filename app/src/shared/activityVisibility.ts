/** Keep internal recovery/error chatter out of live progress labels. */
const INTERNAL_ACTIVITY = /\b(?:failed|failure|error|retry(?:ing)?|invalid|duplicate|timed?\s*out|timeout|skipping|avoiding|unable|could not)\b|did not finish/i

export function isUserFacingActivity(activity: string | undefined): activity is string {
  if (!activity?.trim()) return false
  return !INTERNAL_ACTIVITY.test(activity)
}
