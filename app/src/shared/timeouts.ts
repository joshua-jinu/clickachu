export function isApprovalTimeout(summary: string | undefined): boolean {
  return /approval.*timed out/i.test(summary ?? '')
}
