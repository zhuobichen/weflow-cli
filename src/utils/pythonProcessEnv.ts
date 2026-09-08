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

export function safeSubprocessError(error: unknown, label = '子进程执行失败'): string {
  const value = error as { code?: unknown; killed?: boolean; signal?: unknown }
  if (value?.code === 'ENOENT') return `${label}: 运行时或命令不可用`
  if (value?.code === 'ETIMEDOUT' || value?.killed || value?.signal) return `${label}: 执行超时或被终止`
  if (typeof value?.code === 'number' || /^\d+$/.test(String(value?.code || ''))) {
    return `${label} (exit ${String(value.code)})`
  }
  return label
}
