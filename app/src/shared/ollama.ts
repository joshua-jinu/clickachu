import type { OllamaConnectionProfile, Settings } from './protocol'

export const LOCAL_OLLAMA_URL = 'http://127.0.0.1:11434/v1'

export function isLocalOllama(value: string): boolean {
  try {
    const host = new URL(value).hostname
    return host === 'localhost' || host === '[::1]' || /^127\./.test(host)
  } catch {
    return false
  }
}

/** Accept an IP/hostname, server root, or complete OpenAI endpoint. */
export function normalizeOllamaUrl(value: string): string {
  const trimmed = value.trim()
  if (!trimmed) throw new Error('Enter the other computer’s IP address or server URL.')
  const explicitScheme = trimmed.includes('://')
  const url = new URL(explicitScheme ? trimmed : `http://${trimmed}`)
  if (
    !['http:', 'https:'].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash
  ) {
    throw new Error(
      'Use an HTTP or HTTPS server address without credentials, a query, or a fragment.',
    )
  }
  if (!explicitScheme && !url.port) url.port = '11434'
  const path = url.pathname.replace(/\/+$/, '')
  url.pathname = path.endsWith('/v1') ? path : `${path}/v1`
  return url.toString().replace(/\/+$/, '')
}

export function switchOllamaConnection(form: Settings, local: boolean): Settings {
  if (isLocalOllama(form.ollama_base_url) === local) return form
  const current: OllamaConnectionProfile = {
    base_url: form.ollama_base_url,
    model: form.ollama_model,
    summary_model: form.ollama_summary_model,
    cpu_only: form.ollama_cpu_only,
  }
  const next = (local ? form.ollama_local_profile : form.ollama_remote_profile) ?? {
    base_url: local ? LOCAL_OLLAMA_URL : '',
    model: local ? 'llama3.2:3b' : '',
    summary_model: '',
    cpu_only: false,
  }
  // An empty remote draft has no endpoint yet; never persist it as a remembered profile.
  let baseUrl = ''
  try {
    baseUrl = normalizeOllamaUrl(current.base_url)
  } catch {
    /* Incomplete draft. */
  }
  return {
    ...form,
    ...(baseUrl
      ? {
          [local ? 'ollama_remote_profile' : 'ollama_local_profile']: {
            ...current,
            base_url: baseUrl,
          },
        }
      : {}),
    ollama_base_url: next.base_url,
    ollama_model: next.model,
    ollama_summary_model: next.summary_model,
    ollama_cpu_only: next.cpu_only,
  }
}
