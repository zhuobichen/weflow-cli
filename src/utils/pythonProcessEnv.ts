const INTERNAL_SECRET_KEYS = [
  'WEFLOW_DB_PATH',
  'WEFLOW_NT_KEY',
  'WEFLOW_NT_SALT',
  'WEFLOW_CONTACT_DB',
  'WEFLOW_CONTACT_KEY',
  'WEFLOW_CONTACT_SALT',
  'WEFLOW_TALKER',
  'WEFLOW_OWN_WXID',
  'WEFLOW_NT_PASSPHRASE',
  'WEFLOW_VAULT_QUESTION',
  'WEFLOW_SEARCH_QUERY',
  'WEFLOW_RAG_QUESTION',
  'WEFLOW_RAG_TALKER',
  'WEFLOW_REPORT_TALKERS',
  'WEFLOW_SCAN_ROOT',
  'WEFLOW_QUERY_KEYWORD',
  'WEFLOW_QUERY_USERNAMES',
  'WEFLOW_EXPORT_NAME',
  'WEFLOW_EXPORT_OUTPUT',
  'WEFLOW_EXPORT_CACHE_DIR',
  'WEFLOW_EXPORT_ACCOUNT_DIR',
  'WEFLOW_EXPORT_DATE',
] as const

export function createPythonProcessEnv(
  overrides: Record<string, string | undefined> = {},
): NodeJS.ProcessEnv {
  const env: NodeJS.ProcessEnv = { ...process.env, PYTHONIOENCODING: 'utf-8' }
  for (const key of INTERNAL_SECRET_KEYS) delete env[key]
  for (const [key, value] of Object.entries(overrides)) {
    if (value) env[key] = value
  }
  return env
}

/**
 * Last non-empty stderr line, with anything key-shaped redacted.
 *
 * The scripts report the actual cause on stderr ("缺少公众号数据库密钥: ..."),
 * and discarding it left operators with a bare exit code to search for.
 */
function stderrTail(stderr: unknown): string {
  const text = typeof stderr === 'string'
    ? stderr
    : Buffer.isBuffer(stderr) ? stderr.toString('utf8') : ''
  const lines = text.split('\n').map(line => line.trim()).filter(Boolean)
  const line = lines[lines.length - 1] || ''
  return line.replace(/\b[0-9a-fA-F]{32,}\b/g, '<已隐藏>').slice(0, 200)
}

export function safeSubprocessError(error: unknown, label = '子进程执行失败'): string {
  const value = error as { code?: unknown; killed?: boolean; signal?: unknown; stderr?: unknown }
  if (value?.code === 'ENOENT') return `${label}: 运行时或命令不可用`
  if (value?.code === 'ETIMEDOUT' || value?.killed || value?.signal) return `${label}: 执行超时或被终止`
  if (typeof value?.code === 'number' || /^\d+$/.test(String(value?.code || ''))) {
    const detail = stderrTail(value?.stderr)
    return `${label} (exit ${String(value.code)})${detail ? `: ${detail}` : ''}`
  }
  return label
}
