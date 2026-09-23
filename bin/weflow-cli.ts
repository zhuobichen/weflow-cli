#!/usr/bin/env node
import { Command } from 'commander'
import chalk from 'chalk'
import inquirer from 'inquirer'
import { basename, join } from 'path'
import { existsSync, readFileSync, statSync, writeFileSync } from 'fs'
import { homedir } from 'os'
import { SYNC_SCHEMA, SYNC_SOURCE, syncStateStore } from '../src/services/syncState.js'
import { SyncRangeRequiredError, runSync } from '../src/services/syncService.js'
import { dbPathService } from '../src/core/dbPathService.js'
import { keyService } from '../src/core/keyService.js'
import { NtCore } from '../src/core/ntCore.js'
import { configService } from '../src/services/configService.js'
import { chatService } from '../src/services/chatService.js'
import { exportService } from '../src/services/exportService.js'
import { writeEvidencePackage } from '../src/services/evidenceService.js'
import { resolveTalker as resolveTalkerCore } from '../src/utils/talkerUtils.js'
import { getPythonCommand } from '../src/utils/python.js'
import { createPythonProcessEnv, safeSubprocessError } from '../src/utils/pythonProcessEnv.js'
import { DateRangeError, parseLocalDateOrIso, resolveExportDateRange } from '../src/utils/dateRange.js'
import { resolvePackageRoot as resolvePackageRootFrom } from '../src/utils/packageRoot.js'
import { WechatMessageService } from '../src/services/wechatMessageService.js'
import { whitelistService, MAX_TEXT_LENGTH } from '../src/services/whitelistService.js'
import { applyDerivedNtKeys, enableFavorites, detectFavDbPath } from '../src/services/initKeyService.js'
import type { ChatSession } from '../src/types.js'

const program = new Command()

function resolvePackageRoot(): string {
  return resolvePackageRootFrom(import.meta.url)
}

/**
 * Version reported by `--version` and `capabilities`.
 *
 * Read from package.json instead of written here. A literal has to be updated
 * by hand on every release, and when that is forgotten `--version` lies about
 * what is installed - which is exactly what happened: the 1.6.0 package
 * shipped a hardcoded `1.5.1`, so a user's bug report could not be told apart
 * from a stale install, and diagnosing it needed a clean install to disprove.
 */
function resolveCliVersion(): string {
  try {
    const pkg = JSON.parse(readFileSync(join(resolvePackageRoot(), 'package.json'), 'utf8'))
    return String(pkg.version || '0.0.0')
  } catch {
    return '0.0.0'
  }
}

const CLI_VERSION = resolveCliVersion()

function pythonProcessEnv(apiKey?: string, variable = 'DEEPSEEK_API_KEY'): NodeJS.ProcessEnv {
  return createPythonProcessEnv(apiKey ? { [variable]: apiKey } : {})
}

function parseCliInteger(value: unknown, field: string, minimum: number, maximum: number, json = false): number {
  const parsed = Number(value)
  if (Number.isInteger(parsed) && parsed >= minimum && parsed <= maximum) return parsed
  const error = `${field} 必须是 ${minimum}-${maximum} 的整数`
  if (json) console.log(JSON.stringify({ success: false, code: 'INVALID_ARGUMENT', field, error }))
  else console.log(chalk.red(error))
  process.exit(1)
}

function requireCliDate(value: string, json = false): string {
  try {
    parseLocalDateOrIso(value)
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) throw new DateRangeError('INVALID_DATE', '日期无效')
    return value
  } catch {
    if (json) console.log(JSON.stringify({ success: false, code: 'INVALID_DATE', field: 'date' }))
    else console.log(chalk.red('date 必须是有效的 YYYY-MM-DD 日期'))
    process.exit(1)
  }
}

async function runConfirmedPythonMutation(options: {
  action: string
  script: string
  args: string[]
  cliOptions: { dryRun?: boolean; yes?: boolean; json?: boolean }
  preview: Record<string, unknown>
  confirmationMessage: string
  apiKey?: string
  apiKeyVariable?: string
  timeout?: number
}): Promise<void> {
  const preview = { success: true, dryRun: true, action: options.action, ...options.preview }
  if (options.cliOptions.dryRun) {
    if (options.cliOptions.json) console.log(JSON.stringify(preview))
    else console.log(chalk.cyan(options.confirmationMessage.replace(/^确认/, '预览：')))
    return
  }
  if (!options.cliOptions.yes) {
    if (options.cliOptions.json) {
      console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
      process.exit(1)
    }
    const { confirmed } = await inquirer.prompt([{
      type: 'confirm',
      name: 'confirmed',
      message: options.confirmationMessage,
      default: false,
    }])
    if (!confirmed) {
      console.log(chalk.gray('已取消'))
      return
    }
  }

  const { execFile } = await import('child_process')
  const { promisify } = await import('util')
  try {
    const { stdout } = await promisify(execFile)(getPythonCommand(), [options.script, ...options.args], {
      timeout: options.timeout || 120_000,
      maxBuffer: 50 * 1024 * 1024,
      env: pythonProcessEnv(options.apiKey, options.apiKeyVariable || 'DEEPSEEK_API_KEY'),
    })
    if (options.cliOptions.json) console.log(JSON.stringify({ success: true, action: options.action }))
    else console.log(stdout)
  } catch (error) {
    if (options.cliOptions.json) {
      console.log(JSON.stringify({ success: false, code: 'PYTHON_MUTATION_FAILED', action: options.action, error: safeSubprocessError(error) }))
    } else {
      console.error(chalk.red(`\n✗ ${safeSubprocessError(error)}`))
    }
    process.exit(1)
  }
}

async function openLocalUrl(url: string): Promise<void> {
  const { spawn } = await import('child_process')
  const command = process.platform === 'win32' ? 'rundll32.exe' : process.platform === 'darwin' ? 'open' : 'xdg-open'
  const args = process.platform === 'win32' ? ['url.dll,FileProtocolHandler', url] : [url]
  const child = spawn(command, args, { detached: true, stdio: 'ignore', windowsHide: true })
  child.unref()
}

async function getDailyReaderStatus(port: number): Promise<{ running: boolean; date: string | null }> {
  try {
    const response = await fetch(`http://127.0.0.1:${port}/api/status`, { signal: AbortSignal.timeout(500) })
    const status = await response.json() as { ok?: boolean; service?: string; date?: string }
    const running = response.ok && status.ok === true && status.service === 'weflow-daily-reader'
    return { running, date: running && status.date ? status.date : null }
  } catch {
    return { running: false, date: null }
  }
}

async function startDetachedDailyReader(script: string, date: string, port: number): Promise<{
  success: boolean
  started: boolean
  alreadyRunning?: boolean
  code?: string
}> {
  const existing = await getDailyReaderStatus(port)
  if (existing.running) {
    if (existing.date === date) return { success: true, started: false, alreadyRunning: true }
    return { success: false, started: false, code: 'DAILY_READER_PORT_IN_USE' }
  }

  const dateDir = join(resolvePackageRoot(), 'output', 'biz-daily', date)
  if (!existsSync(dateDir)) return { success: false, started: false, code: 'DAILY_READER_DATE_NOT_FOUND' }

  const { spawn } = await import('child_process')
  const child = spawn(getPythonCommand(), [script, '--date', date, '--port', String(port)], {
    detached: true,
    stdio: 'ignore',
    windowsHide: true,
    env: pythonProcessEnv(),
  })
  let spawnFailed = false
  child.once('error', () => { spawnFailed = true })

  for (let attempt = 0; attempt < 30; attempt++) {
    if (spawnFailed || child.exitCode !== null) break
    const status = await getDailyReaderStatus(port)
    if (status.running && status.date === date) {
      child.unref()
      return { success: true, started: true }
    }
    await new Promise(resolve => setTimeout(resolve, 100))
  }

  if (child.exitCode === null && !child.killed) child.kill()
  return { success: false, started: false, code: 'DAILY_READER_START_FAILED' }
}

/**
 * 尝试从 WeFlow 桌面版配置中读取已保存的密钥
 */
function tryReadWeFlowKey(): string | null {
  const candidates = [
    join(homedir(), 'AppData', 'Roaming', 'WeFlow', 'WeFlow-config.json'),
    join(homedir(), 'AppData', 'Roaming', 'weflow', 'WeFlow-config.json'),
    join(homedir(), '.weflow', 'config.json'),
  ]

  for (const configPath of candidates) {
    try {
      if (!existsSync(configPath)) continue
      const raw = readFileSync(configPath, 'utf8')
      const config = JSON.parse(raw)

      // WeFlow 使用 electron-store，decryptKey 可能是明文或加密的
      let key = config.decryptKey || ''
      // 如果是 lock: 前缀，说明是加密的，无法直接使用
      if (typeof key === 'string' && key.startsWith('lock:')) {
        continue
      }
      // 如果是 safe: 前缀，说明是 safeStorage 加密的，也无法直接使用
      if (typeof key === 'string' && key.startsWith('safe:')) {
        continue
      }
      // 明文 key
      if (typeof key === 'string' && key.length === 64) {
        return key
      }

      // 检查 wxidConfigs 中的 key
      const wxidConfigs = config.wxidConfigs || {}
      for (const [_wxid, cfg] of Object.entries(wxidConfigs) as [string, any][]) {
        const cfgKey = cfg.decryptKey || ''
        if (typeof cfgKey === 'string' && cfgKey.length === 64 && !cfgKey.startsWith('lock:') && !cfgKey.startsWith('safe:')) {
          return cfgKey
        }
      }
    } catch {
      continue
    }
  }
  return null
}

/**
 * CLI 包装的 talker 解析 (wxid / 昵称 / 备注名 / 序号)。
 * 委托给 src/utils/talkerUtils 的核心实现，外加 chalk 友好输出。
 */
async function resolveTalker(input: string, quiet = false, nonInteractive = false): Promise<string> {
  try {
    const result = await resolveTalkerCore(input, { interactive: !nonInteractive })
    // 显示解析结果（已知格式跳过打印）
    if (!quiet && !input.startsWith('wxid_') && !input.includes('@chatroom') && !input.includes('@openim')) {
      const isNum = /^\[?\d+\]?$/.test(input)
      const sessions = await chatService.listSessions(undefined, 50)
      const match = sessions.find(s => s.username === result)
      if (match) {
        const prefix = isNum ? `序号[${input.replace(/[\[\]]/g, '')}] ` : ''
        console.log(chalk.gray(`  → ${prefix}${match.displayName} (${match.username})`))
      }
    }
    return result
  } catch (e: any) {
    if (quiet) {
      console.log(JSON.stringify({ success: false, code: 'TALKER_RESOLUTION_FAILED', error: e.message }))
      process.exit(1)
    }
    console.log(chalk.red(`\n❌ ${e.message}\n`))
    process.exit(1)
  }
}

/**
 * 较新微信版本派生模式: 内存中不再有 x'<key><salt>' 文本, 内存扫描匹配不到密钥。
 * 各库密钥改为从全库 passphrase 派生, 逻辑见 src/services/initKeyService.ts。
 */

program
  .name('weflow-cli')
  .description('WeFlow CLI - 微信聊天记录命令行查询与导出工具')
  .version(CLI_VERSION)

program
  .command('capabilities')
  .description('输出 AI 可调用的功能能力清单')
  .option('--json', '输出 JSON 格式')
  .action((opts) => {
    const data = {
      schema: 'weflow-capabilities/v1',
      version: CLI_VERSION,
      read: {
        sessions: { cli: 'sessions --json', mcp: 'wechat.list_sessions' },
        messages: { cli: 'messages <talker> --json', mcp: 'wechat.export_messages' },
        contacts: { cli: 'contacts --json' },
        exports: {
          cli: 'export <talker> <json|txt|html|excel>',
          mcp: 'wechat.export_messages',
          versionedContract: 'weflow-message/v1',
          rawContractPreserved: true,
          coverage: ['requestedFrom', 'requestedTo', 'requestedLimit', 'returned', 'mayHaveMore', 'oldestCreateTime', 'newestCreateTime'],
          incrementalRead: { mode: 'overlapping-time-window', stableCursor: false },
        },
        favorites: {
          cli: 'fav list --json',
          export: 'fav export <markdown|json> --json-result --output <local-file>',
          mcp: 'wechat.search_favorites',
        },
        configuration: { cli: 'config show --json', secretsIncluded: false },
        accessControl: {
          whitelist: 'whitelist list --json',
          blacklist: 'blacklist list --json',
          sensitiveLocalIdentifiers: true,
        },
        moments: {
          timeline: 'sns timeline --json',
          users: 'sns users --json',
          stats: 'sns stats --json',
        },
        daily: {
          preview: 'daily --no-ai --dry-run --json',
          execute: 'daily --no-ai --yes --json',
          confirmationRequired: true,
          logs: 'stderr',
          result: 'stdout',
        },
        dailyReader: { cli: 'daily-server --status --json' },
        dailyStats: { cli: 'daily-stats --json' },
        diagnostics: { cli: 'check --json' },
        todos: { cli: 'todos list --json' },
        awaiting: {
          cli: 'awaiting --yes',
          preview: 'awaiting --dry-run --json',
          readsLocalChat: true,
          invokesAI: true,
          writesNothing: true,
          confirmationRequired: true,
        },
        knowledge: { cli: 'search <query> --yes --json', preview: 'search <query> --dry-run --json', output: 'json', mcp: 'wechat.search_articles' },
      },
      primitives: {
        decide: {
          cli: 'decide --request <file> --yes | decide --over <glob> --ask <text> --yes',
          preview: 'decide --dry-run',
          // 两条路的读法**不一样**，所以不能只写一个 false：
          // --request 完全由调用方给，--over 会读匹配到的本地文件。
          readsLocalData: { request: false, over: true },
          invokesAI: true,
          sendsCallerProvidedState: true,
          confirmationRequired: true,
          mcpExposed: false,
          whyNotMcp: 'an MCP client must not be able to drive local outbound calls',
        },
      },
      workflows: {
        initialization: {
          preview: 'init --dry-run --json',
          execute: 'init',
          interactiveRequired: true,
          machineExecutionAllowed: false,
          previewExposesPaths: false,
        },
        evidencePackage: { cli: 'evidence <talker> --json --non-interactive', localOnly: true },
        sync: {
          preview: 'sync run <talker> --dry-run --json',
          execute: 'sync run <talker> --yes --json',
          status: 'sync status [<talker>] --json',
          verify: 'sync verify <talker> --json',
          confirmationRequired: true,
          readsLocalData: true,
          invokesAI: false,
          sideEffects: ['local-checkpoint-file'],
          stateSchema: 'weflow-sync/v1',
          jobSchema: 'weflow-job/v1',
          stateLocation: 'local-user-config-dir',
          previewResolvesTalker: false,
          previewExposesLocalPaths: false,
          // D-027: a stable cursor must be backed by every database backend
          // before it is advertised. This exposes overlapping windows only.
          stableCursor: false,
          coverageValues: ['complete', 'unverified', 'partial'],
          partialCompletesSuccessfully: false,
        },
        aiAnalysis: {
          preview: 'evidence-review <talker> --dry-run --json',
          execute: 'evidence-review <talker> --yes --json',
          explicitOptIn: true,
          cloudRequiresAllowCloud: true,
          confirmationRequired: true,
        },
        messaging: {
          preview: 'send <target> <message> --dry-run --json',
          execute: 'send <target> <message> --yes --json',
          confirmationRequired: true,
          channel: 'official-bot-existing-conversation',
        },
        todoMutations: {
          preview: 'todos <done|undone|rm> <id> --dry-run --json',
          execute: 'todos <done|undone|rm> <id> --yes --json',
          confirmationRequired: true,
        },
        accessControlMutations: {
          preview: '<whitelist|blacklist> <add|rm> <target> --dry-run --json',
          execute: '<whitelist|blacklist> <add|rm> <target> --yes --json',
          confirmationRequired: true,
          sensitiveLocalIdentifiers: true,
        },
        secretConfiguration: {
          preview: 'config set-env <key> <environment> --dry-run --json',
          execute: 'config set-env <key> <environment> --yes --json',
          favoriteKeyPreview: 'fav set-key --from-env <environment> --dry-run --json',
          favoriteKeyExecute: 'fav set-key --from-env <environment> --yes --json',
          valuesInArguments: false,
          confirmationRequired: true,
        },
        databaseKeyReset: {
          preview: 'config forget-keys --dry-run --json',
          cli: 'config forget-keys --yes --json',
          confirmationRequired: true,
        },
        assistantDaemon: {
          status: 'assistant status --json',
          preview: 'assistant <start|stop> --dry-run --json',
          execute: 'assistant <start|stop> --yes --json',
          confirmationRequired: true,
        },
        messageChannelAuthentication: {
          loginPreview: 'login-wechat --dry-run --json',
          loginExecute: 'login-wechat --yes',
          loginInteractiveRequired: true,
          logoutPreview: 'logout-wechat --dry-run --json',
          logoutExecute: 'logout-wechat --yes --json',
          confirmationRequired: true,
        },
        interactiveKeyCapture: {
          previews: ['dbkey --dry-run --json', 'sns capture-key --dry-run --json'],
          executes: ['dbkey --yes', 'sns capture-key --yes'],
          interactiveRequired: true,
          machineExecutionAllowed: false,
        },
        foregroundMessageProcesses: {
          previews: ['listen --dry-run --json', 'assistant run --dry-run --json'],
          executes: ['listen --yes', 'assistant run --yes'],
          interactiveRequired: true,
          machineExecutionAllowed: false,
        },
        vaultSync: {
          preview: 'vault sync --dry-run --json',
          execute: 'vault sync --yes --json',
          confirmationRequired: true,
          previewExposesFileNames: false,
          resultExposesRemote: false,
        },
        vaultInitialization: {
          preview: 'vault init --path <local-directory> --dry-run --json',
          execute: 'vault init --path <local-directory> --yes --json',
          confirmationRequired: true,
          previewExposesPaths: false,
          overwritesManagedFiles: true,
        },
        semanticIndex: {
          preview: 'search-index --dry-run --json',
          execute: 'search-index --yes --json',
          fullRebuild: 'search-index --full --yes --json',
          confirmationRequired: true,
          sendsTextToEmbeddingProvider: true,
        },
        knowledgePipeline: {
          preview: 'pipeline run --no-ai --dry-run --json',
          execute: 'pipeline run --no-ai --yes --json',
          confirmationRequired: true,
          supportsSourceFilter: true,
          aiCanBeDisabled: true,
        },
        vaultContentMutations: {
          commands: ['vault enrich', 'vault notes', 'vault tag', 'vault sync-weread', 'vault promote ideas', 'vault promote all', 'wiki compile'],
          previewFlag: '--dry-run --json',
          executeFlag: '--yes --json',
          confirmationRequired: true,
          aiRequiresExplicitOptIn: true,
        },
        dailyFavoriteMutations: {
          commands: ['daily favorites sync', 'daily favorites add', 'daily favorites remove'],
          previewFlag: '--dry-run --json',
          executeFlag: '--yes --json',
          confirmationRequired: true,
          previewExposesArticleNames: false,
        },
        dailyReader: {
          status: 'daily-server --status --json',
          previewStart: 'daily-server --dry-run --json',
          executeStart: 'daily-server --yes --json',
          confirmationRequired: true,
          loopbackOnly: true,
        },
        reportGeneration: {
          commands: ['report', 'review', 'annual-report', 'chat-stats'],
          previewFlag: '--dry-run --json',
          executeFlag: '--yes --json',
          confirmationRequired: true,
          resultExposesContent: false,
        },
        todoExtraction: {
          preview: 'todos extract --days <n> --dry-run --json',
          execute: 'todos extract --days <n> --yes --json',
          confirmationRequired: true,
          sendsSelectedChatToAi: true,
          resultExposesTodoText: false,
        },
        vaultRag: {
          preview: 'vault rag <question> --dry-run --json',
          execute: 'vault rag <question> --yes --json',
          confirmationRequired: true,
          sendsSelectedKnowledgeToAi: true,
          questionInProcessArguments: false,
        },
        semanticQuery: {
          preview: 'search <query> --dry-run --json',
          execute: 'search <query> --yes --json',
          confirmationRequired: true,
          mayUseCloudEmbedding: true,
          queryInProcessArguments: false,
        },
        ragChat: {
          preview: 'chat <question> --dry-run --json',
          execute: 'chat <question> --yes --json',
          confirmationRequired: true,
          sendsSelectedKnowledgeToAi: true,
          questionInProcessArguments: false,
          interactiveModeMachineExecutionAllowed: false,
        },
        diagnostics: {
          accountScan: 'scan --json',
          assistantLogStatus: 'assistant log --json',
          sensitiveValuesInStatus: false,
        },
        mcpConfiguration: {
          read: 'mcp-config',
          previewWrite: 'mcp-config --output <file> --dry-run --json-result',
          executeWrite: 'mcp-config --output <file> --yes --json-result',
          confirmationRequired: true,
        },
        destructiveClear: {
          commands: ['config clear', 'whitelist clear', 'blacklist clear', 'audit clear'],
          previewFlag: '--dry-run --json',
          confirmationFlag: '--yes',
          confirmationRequired: true,
          previewExposesEntries: false,
        },
      },
      safety: {
        localByDefault: true,
        aiDisabledByDefaultForExport: true,
        unknownMessageTypesPreserved: true,
        nonInteractiveTalkerResolution: true,
        mcpDefaultReadOnly: true,
        mcpMessageLimit: { default: 100, max: 1000 },
      },
    }
    console.log(JSON.stringify(data, null, 2))
  })

// ==================== init ====================
program
  .command('init')
  .description('自动检测微信数据目录并提取解密密钥')
  .option('-p, --path <path>', '微信数据目录、账号目录或 db_storage 目录')
  .option('--search-drives', '跨本机磁盘按标准微信目录名搜索（耗时较长）')
  .option('--full-scan', '目录名被修改时，使用深度磁盘结构扫描（耗时较长）')
  .option('--refresh', '忽略已有配置并重新初始化')
  .option('--test-missing-keys', '仅在本次运行模拟密钥缺失，不修改已保存配置')
  .option('--dry-run', '仅预览，不连接数据库、扫描磁盘、捕获密钥或修改配置')
  .option('--json', '输出机器可读预览；实际初始化必须在交互终端执行')
  .action(async (opts) => {
    const preview = {
      success: true,
      dryRun: true,
      action: 'initialize',
      interactiveRequired: true,
      configured: configService.isConfigured(),
      refreshRequested: Boolean(opts.refresh),
      explicitPathProvided: Boolean(opts.path),
      driveSearchRequested: Boolean(opts.searchDrives),
      fullScanRequested: Boolean(opts.fullScan),
      missingKeyTestRequested: Boolean(opts.testMissingKeys),
      mayScanProcessMemory: true,
      mayWriteConfiguration: true,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan('初始化预览：可能检查数据库、搜索数据目录、捕获密钥并更新本地配置。'))
      return
    }
    if (opts.json) {
      console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'INTERACTIVE_REQUIRED' }))
      process.exit(1)
    }
    console.log(chalk.cyan('🔧 WeFlow CLI 初始化\n'))

    if (opts.testMissingKeys) {
      configService.maskDatabaseKeysForTesting()
      console.log(chalk.yellow('测试模式：本次运行忽略已保存的数据库密钥。'))
      console.log(chalk.gray('测试失败不会清除磁盘上的现有密钥配置。\n'))
    }

    if (!opts.refresh && !opts.path && !opts.testMissingKeys && configService.isConfigured()) {
      const existingConnection = await chatService.connect()
      if (existingConnection.success) {
        console.log(chalk.green('✓ 已验证现有本地配置可用，跳过密钥捕获。'))
        console.log(chalk.gray('  可直接运行 weflow-cli sessions、messages 等命令。'))
        console.log(chalk.gray('  仅在微信迁移、切换账号或访问失败后运行 weflow-cli init --refresh。'))
        return
      }
      console.log(chalk.yellow('  已发现现有配置，但无法验证数据库访问；继续重新初始化。'))
    }

    // Step 0: 检测微信版本
    console.log(chalk.yellow('步骤 0/3: 检测微信版本...'))
    const version = await keyService.detectWeChatVersion()

    if (version) {
      console.log(chalk.green(`✓ 检测到微信 ${version} 版本`))
    } else {
      // 没有运行中的微信，尝试已安装的版本
      console.log(chalk.gray('  未检测到运行中的微信进程'))
      // 检查已有配置或数据目录
      const existingPath = configService.get('dbPath')
      if (existingPath && existsSync(existingPath)) {
        console.log(chalk.green(`  使用已配置的数据目录: ${existingPath}`))
      }
    }

    if (version === '3.x') {
      // ====== 3.x 初始化 ======
      console.log(chalk.yellow('\n步骤 1/3: 提取 3.x 数据库密钥...'))
      const result = await keyService.extract3xKey((msg) => {
        console.log(chalk.gray(`  ${msg}`))
      })

      if (!result.success || !result.key) {
        console.log(chalk.red(`\n✗ 密钥提取失败: ${result.error}`))
        console.log(chalk.gray('\n解决方案:'))
        console.log(chalk.gray('  1. 确保 WeChat 3.x (WeChat.exe) 已登录'))
        console.log(chalk.gray('  2. 确保 Python + pywxdump 已安装'))
        console.log(chalk.gray('  3. 或手动指定密钥: weflow-cli config set decryptKey3x <64位密钥>'))
        process.exit(1)
      }

      const msgDir = result.msgDir || ''
      const wxDir = result.wxDir || ''
      const wxid = result.wxid || ''

      configService.set('decryptKey3x', result.key)
      configService.set('dataVersion', '3.x')

      // 查找 MSG0.db
      const msg0Path = join(msgDir, 'Multi', 'MSG0.db')
      if (existsSync(msg0Path)) {
        configService.set('dbPath3x', msg0Path)
        console.log(chalk.green(`✓ 数据库: ${msg0Path}`))
      }

      if (wxid) configService.set('wxid', wxid)
      if (wxDir) configService.set('dbPath', wxDir)

      console.log(chalk.green('\n✓ 密钥获取成功!'))
      console.log(chalk.cyan('\n=============================='))
      console.log(chalk.cyan('初始化完成! (3.x 模式)'))
      console.log(chalk.cyan('=============================='))
      console.log(chalk.white(`数据目录: ${wxDir}`))
      console.log(chalk.white(`消息数据库: ${msg0Path}`))
      console.log(chalk.white(`账号: ${wxid}`))
      console.log(chalk.white('密钥: 已安全保存'))

    } else {
      // ====== 4.x 初始化 (原有逻辑) ======
      console.log(chalk.yellow('\n步骤 1/3: 检测微信数据目录...'))
      const configuredPath = opts.path || configService.get('dbPath')
      const configuredDataRoot = configuredPath
        ? dbPathService.resolveDataRoot(configuredPath)
        : null
      if (configuredPath && !configuredDataRoot) {
        console.log(chalk.yellow('  已检查指定路径，但未识别到有效的微信数据结构；继续自动搜索...'))
      }
      if (!configuredDataRoot) {
        console.log(chalk.gray('  正在自动检查常见的微信数据目录位置...'))
      }
      if (!configuredDataRoot && opts.searchDrives) {
        console.log(chalk.gray('  将跨本机磁盘按标准目录名搜索，过程可能需要较长时间...'))
      }
      if (!configuredDataRoot && opts.fullScan) {
        console.log(chalk.gray('  目录名未命中时将继续进行深度结构扫描，过程可能需要较长时间...'))
      }
      const detected = configuredDataRoot
        ? { success: true, path: configuredDataRoot }
        : await dbPathService.autoDetect({ searchDrives: opts.searchDrives, fullScan: opts.fullScan })
      if (!detected.success || !detected.path) {
        console.log(chalk.red('✗ 未检测到微信数据目录'))
        if (!opts.searchDrives && !opts.fullScan) {
          console.log(chalk.gray('  已完成常见位置搜索。'))
          console.log(chalk.cyan('  下一步: weflow-cli init --search-drives'))
          console.log(chalk.gray('  该模式会跨本机磁盘按 xwechat_files 或 WeChat Files 名称搜索。'))
        } else if (!opts.fullScan) {
          console.log(chalk.gray('  已完成标准目录名的跨盘搜索。'))
          console.log(chalk.cyan('  下一步: weflow-cli init --full-scan'))
          console.log(chalk.gray('  此模式会继续按目录结构扫描可访问磁盘，耗时较长。'))
        } else {
          console.log(chalk.gray('  已完成深度结构扫描，仍未找到可识别的数据目录。'))
          console.log(chalk.cyan('  请指定数据根、账号目录或 db_storage 目录:'))
          console.log(chalk.cyan('  weflow-cli init --path "D:\\微信数据目录"'))
        }
        process.exit(1)
      }
      console.log(chalk.green(`✓ 检测到数据目录: ${detected.path}`))
      configService.set('dbPath', detected.path)
      configService.set('dataVersion', '4.x')

      // Step 2: 扫描账号
      console.log(chalk.yellow('\n步骤 2/3: 扫描微信账号...'))
      const wxids = dbPathService.scanWxidCandidates(detected.path)
      if (wxids.length === 0) {
        console.log(chalk.red('✗ 未找到微信账号目录'))
        process.exit(1)
      }

      const selectedWxid = wxids[0]
      console.log(chalk.green(`✓ 找到 ${wxids.length} 个账号`))
      for (const w of wxids) {
        const marker = w === selectedWxid ? chalk.green(' → ') : '   '
        const nickname = w.nickname ? ` (${w.nickname})` : ''
        console.log(`${marker}${w.wxid}${nickname}`)
      }
      configService.set('wxid', selectedWxid.wxid)

      // Step 3: 提取密钥
      console.log(chalk.yellow('\n步骤 3/3: 提取数据库解密密钥...'))

      // 优先尝试从 WeFlow 配置读取
      console.log(chalk.gray('  尝试从 WeFlow 桌面版配置读取...'))
      let extractedKey = opts.testMissingKeys ? '' : tryReadWeFlowKey()
      let linuxNtKey = ''
      /** 密钥是本次从 hook 捕获的, 还是沿用已有的 - 摘要要说准, 不能一律报"获取成功" */
      let keyCaptured = false

      if (extractedKey) {
        console.log(chalk.green('  ✓ 从 WeFlow 配置读取到密钥'))
      } else if (process.platform === 'linux') {
        // Linux：/proc 内存扫描提取 NT 密钥（需 root 或 CAP_SYS_PTRACE）
        console.log(chalk.gray('  Linux 模式：扫描微信进程内存提取 NT 密钥...'))
        const ntScan = await NtCore.scan(detected.path)

        if (ntScan.success && ntScan.matched && ntScan.matched.length > 0) {
          console.log(chalk.green(`  ✓ 从内存中匹配到 ${ntScan.matched.length} 个数据库密钥`))
          const primaryDb = ntScan.matched.find((db: any) => db.name === 'message/message_0.db')
            || ntScan.matched.sort((a: any, b: any) => b.size - a.size)[0]
          configService.set('ntDbPath', primaryDb.path)
          configService.set('ntKey', primaryDb.key)
          configService.set('ntSalt', primaryDb.salt)
          console.log(chalk.green(`  ✓ 主数据库: ${primaryDb.name} (${(primaryDb.size / 1024 / 1024).toFixed(1)}MB)`))

          const contactDb = ntScan.matched.find((db: any) => db.name === 'contact/contact.db')
          if (contactDb) {
            configService.set('contactDbPath', contactDb.path)
            configService.set('contactKey', contactDb.key)
            configService.set('contactSalt', contactDb.salt)
            console.log(chalk.green(`  ✓ 联系人数据库: ${contactDb.name}`))
          }
          const snsDb = ntScan.matched.find((db: any) => db.name === 'sns/sns.db')
          if (snsDb) {
            configService.set('snsDbPath', snsDb.path)
            configService.set('snsKey', snsDb.key)
            configService.set('snsSalt', snsDb.salt)
            console.log(chalk.green(`  ✓ 朋友圈数据库: ${snsDb.name}`))
          }
          linuxNtKey = primaryDb.key
        } else {
          // 较新微信版本: /proc 内存扫描同样匹配不到 x'...' 文本, 从已配置 passphrase 派生
          const favPass = configService.get('favPassphrase') || ''
          const scanDbs = (ntScan as any).databases || []
          const wxidDbs = scanDbs.filter((d: any) => d.wxid === selectedWxid.wxid)
          const deriveDbs = wxidDbs.length > 0 ? wxidDbs : scanDbs
          let derivedOk = false
          if (favPass && deriveDbs.length > 0) {
            console.log(chalk.gray('  内存扫描未发现密钥文本 (较新微信密钥体系), 尝试从 passphrase 派生...'))
            derivedOk = await applyDerivedNtKeys(favPass, deriveDbs, (l) => console.log(l))
            if (derivedOk) linuxNtKey = configService.get('ntKey') || ''
          }
          if (!derivedOk) {
            const err = ntScan.error || '未匹配到数据库密钥'
            console.log(chalk.red(`  ✗ ${err}`))
            console.log(chalk.gray('\n  Linux 密钥提取方案：'))
            if (err.includes('PERMISSION_DENIED')) {
              console.log(chalk.gray('  1. 一次性授权（推荐）: sudo setcap cap_sys_ptrace=ep $(which python3)'))
              console.log(chalk.gray('  2. 或用 sudo 运行: sudo weflow-cli init'))
              console.log(chalk.gray('     （sudo 后需修复配置属主: sudo chown -R $(id -u):$(id -g) ~/.weflow-cli）'))
            } else {
              console.log(chalk.gray('  1. 确保微信已启动并完成登录（登录后才会在内存中缓存密钥）'))
              console.log(chalk.gray('  2. 确保以 root 或 CAP_SYS_PTRACE 运行（sudo setcap cap_sys_ptrace=ep $(which python3)）'))
              console.log(chalk.gray('  3. 较新微信版本需配置全库 passphrase: weflow-cli fav set-key --passphrase <64位hex>，再重新 init 自动派生'))
              console.log(chalk.gray('  4. 或用第三方工具提取密钥后手动配置 weflow-cli config set ntKey <密钥>'))
            }
            process.exit(1)
          }
        }
      } else if (process.platform !== 'win32') {
        // macOS：自动提取不可用，引导手动提供密钥
        console.log(chalk.yellow('  ⚠ 当前平台无法自动提取密钥（自动提取支持 Windows/Linux）'))
        console.log(chalk.gray('\n  macOS 手动配置方式：'))
        console.log(chalk.gray('  1. 用第三方工具从运行中的微信进程提取密钥（如 wechat-dump-rs）'))
        console.log(chalk.gray('  2. 或从 Windows 机器上的 WeFlow 配置复制密钥'))
        console.log(chalk.gray('  3. 写入配置: weflow-cli config set decryptKey <64位密钥>'))
        console.log(chalk.gray('  4. 数据目录与账号已完成配置，设置密钥后即可使用 sessions/messages 等命令'))
        process.exit(1)
      } else {
        // 如果微信未运行，提示启动
        if (!version) {
          console.log(chalk.cyan('\n  ══════════════════════════════════════'))
          console.log(chalk.cyan('  请现在启动微信 4.x 并登录！'))
          console.log(chalk.cyan('  ══════════════════════════════════════\n'))
        }
        console.log(chalk.gray('  等待微信进程出现（启动后会自动 hook）...'))

        const keyResult = await keyService.waitForKey(120000, (msg) => {
          console.log(chalk.gray(`  ${msg}`))
        })

        if (keyResult.success && keyResult.key) {
          extractedKey = keyResult.key
          keyCaptured = true
        } else {
          // hook 只在登录瞬间触发; 微信已登录时捕获不到 → 回退到已有密钥。
          // decryptKey 也必须算数: init 成功时写入的就是它, 而 favPassphrase
          // 仅在收藏库存在且验证通过时才会被设置。只看 favPassphrase 会让
          // `init --refresh` 在一把完全可用的密钥面前报「密钥提取失败」——
          // 而那正是 CLI 自己在微信迁移后建议用户执行的命令。
          const fallback = configService.get('favPassphrase') || configService.get('decryptKey') || ''
          if (fallback) {
            console.log(chalk.gray('  Hook 未捕获到密钥 (微信已登录时不再触发密钥函数)'))
            console.log(chalk.gray('  检测到已配置的密钥, 沿用它并重新验证各库...'))
            extractedKey = fallback
          } else {
            console.log(chalk.red(`\n✗ 密钥提取失败: ${keyResult.error}`))
            console.log(chalk.gray('\n解决方案:'))
            console.log(chalk.gray('  1. 退出微信后重新运行 init (hook 需在微信登录瞬间触发, init 会自动等待)'))
            console.log(chalk.gray('  2. 或用第三方工具提取全库 passphrase 后: weflow-cli fav set-key --passphrase <64位hex>, 再重新 init 自动派生'))
            console.log(chalk.gray('  3. 或手动指定密钥: weflow-cli config set decryptKey <64位密钥>'))
            process.exit(1)
          }
        }
      }

      if (extractedKey) {
        configService.set('decryptKey', extractedKey)
        if (keyCaptured) console.log(chalk.green('\n✓ 密钥获取成功!'))
        else console.log(chalk.green('\n✓ 已沿用配置中的密钥 (本次未重新捕获)'))
      } else {
        console.log(chalk.green('\n✓ NT 密钥配置完成!'))
      }

      // Step 4: 尝试扫描 NT 格式数据库 (xwechat_files)
      // 即使检测到传统路径，也可能存在 NT 格式数据库
      // 进入这里已经确认是 4.x 初始化流程。不要依赖步骤 0 的 version：
      // 微信可能是在 init 等待期间才启动，初始 version 会保持为 null。
      // Linux 分支已在步骤 3 完成配置，跳过。
      if (process.platform !== 'linux') {
        console.log(chalk.yellow('\n步骤 4/4: 扫描 NT 格式数据库...'))
        console.log(chalk.gray('  正在从微信内存中匹配数据库密钥...'))

        const ntScanRoot = configService.get('dbPath') || detected.path
        const ntResult = await NtCore.scan(ntScanRoot)
        if (ntResult.success && ntResult.matched && ntResult.matched.length > 0) {
          console.log(chalk.green(`  ✓ 找到 ${ntResult.matched.length} 个 NT 数据库`))

          // 优先选择 message_0.db (主聊天数据库)
          let primaryDb = ntResult.matched.find((db: any) => db.name === 'message/message_0.db')
          if (!primaryDb) {
            // 按大小降序排序，选择最大的数据库
            primaryDb = ntResult.matched.sort((a: any, b: any) => b.size - a.size)[0]
          }

          if (primaryDb) {
            configService.set('ntDbPath', primaryDb.path)
            configService.set('ntKey', primaryDb.key)
            configService.set('ntSalt', primaryDb.salt)
            console.log(chalk.green(`  ✓ 主数据库: ${primaryDb.name} (${(primaryDb.size / 1024 / 1024).toFixed(1)}MB)`))

            // 检查 contact.db 是否已匹配
            const contactDb = ntResult.matched.find((db: any) => db.name === 'contact/contact.db')
            if (contactDb) {
              configService.set('contactDbPath', contactDb.path)
              configService.set('contactKey', contactDb.key)
              configService.set('contactSalt', contactDb.salt)
              console.log(chalk.green(`  ✓ 联系人数据库: ${contactDb.name} (${(contactDb.size / 1024 / 1024).toFixed(1)}MB)`))
            }

            // 检查 sns.db (朋友圈) 是否已匹配
            const snsDb = ntResult.matched.find((db: any) => db.name === 'sns/sns.db')
            if (snsDb) {
              configService.set('snsDbPath', snsDb.path)
              configService.set('snsKey', snsDb.key)
              configService.set('snsSalt', snsDb.salt)
              console.log(chalk.green(`  ✓ 朋友圈数据库: ${snsDb.name} (${(snsDb.size / 1024 / 1024).toFixed(1)}MB)`))
            }

            // 显示所有匹配的数据库
            for (const db of ntResult.matched) {
              const marker = db === primaryDb || db === contactDb || db === snsDb ? chalk.green('  → ') : '     '
              console.log(chalk.gray(`${marker}${db.name} (${(db.size / 1024 / 1024).toFixed(1)}MB)`))
            }
          }

          // 收藏库与聊天库共用 passphrase, 顺手启用收藏功能
          const passForFav = extractedKey || configService.get('decryptKey') || ''
          if (passForFav) await enableFavorites(passForFav, ntResult.databases || [])
        } else {
          console.log(chalk.gray(`  NT 数据库扫描: ${ntResult.error || '未找到匹配的数据库'}`))

          // 较新微信版本: 内存中不再有 x'<key><salt>' 文本, 内存扫描匹配不到密钥,
          // 从 DLL hook 拿到的全库 passphrase 派生各库密钥并逐一验证
          const passphrase = extractedKey || configService.get('decryptKey') || ''
          const allDbs = ntResult.databases || []
          const wxidDbs = allDbs.filter((db: any) => db.wxid === selectedWxid.wxid)
          const deriveDbs = wxidDbs.length > 0 ? wxidDbs : allDbs
          let derivedMessage = false
          if (passphrase && deriveDbs.length > 0) {
            console.log(chalk.gray('  检测到较新微信密钥体系 (内存中无密钥文本), 从 passphrase 派生各库密钥...'))
            derivedMessage = await applyDerivedNtKeys(passphrase, deriveDbs, (l) => console.log(l))
          }

          if (derivedMessage) {
            console.log(chalk.green('  ✓ 派生模式初始化成功 (聊天/联系人/朋友圈库已配置)'))
          } else {
            // 即使密钥未匹配，仍尝试自动发现 sns.db 路径和盐值
            if (ntResult.databases && ntResult.databases.length > 0) {
              const snsDb = ntResult.databases.find((db: any) => db.name === 'sns/sns.db')
              if (snsDb) {
                configService.set('snsDbPath', snsDb.path)
                configService.set('snsSalt', snsDb.salt)
                console.log(chalk.yellow(`  ⚠ 发现朋友圈数据库: ${snsDb.name} (${(snsDb.size / 1024 / 1024).toFixed(1)}MB)`))
                console.log(chalk.gray('    密钥未匹配，请运行: weflow-cli sns capture-key'))
              }
            }
            console.log(chalk.gray('  提示: 可以稍后运行 weflow-cli init 重新扫描'))
          }
        }
      }

      console.log(chalk.cyan('\n=============================='))
      console.log(chalk.cyan('初始化完成! (4.x 模式)'))
      console.log(chalk.cyan('=============================='))
      console.log(chalk.white(`数据目录: ${detected.path}`))
      console.log(chalk.white(`账号: ${selectedWxid.wxid}${selectedWxid.nickname ? ` (${selectedWxid.nickname})` : ''}`))
      const displayKey = extractedKey || linuxNtKey
      console.log(chalk.white('密钥: 已安全保存'))
      const ntDbPath = configService.get('ntDbPath')
      if (ntDbPath) {
        console.log(chalk.white(`NT 数据库: ${ntDbPath}`))
      }
    }

    console.log(chalk.gray('\n现在可以使用以下命令:'))
    console.log(chalk.gray('  weflow-cli sessions        查看会话列表'))
    console.log(chalk.gray('  weflow-cli messages <talker> 查看消息'))
    console.log(chalk.gray('  weflow-cli contacts        查看联系人'))
    console.log(chalk.gray('  weflow-cli export <talker> <format> 导出聊天记录'))
  })

// ==================== config ====================
const configCmd = program
  .command('config')
  .description('查看或修改配置')

const configurableKeys = [
  'dbPath', 'decryptKey', 'dbPath3x', 'decryptKey3x', 'dataVersion', 'wxid',
  'ntDbPath', 'ntKey', 'ntSalt', 'contactDbPath', 'contactKey', 'contactSalt',
  'vaultRepo', 'aiEngine', 'aiBaseUrl', 'aiModel', 'deepseekApiKey', 'typesafeApiKey',
  'dashscopeApiKey', 'favPassphrase',
  'wereadApiKey',
  'assistantPrivacy', 'assistantWhitelist', 'assistantGroupWhitelist',
  'assistantGroupRequireMention', 'assistantFastRoute',
  'dailySources', 'dailySourceCategories',
  'dailyExcludeTopics', 'dailyAiEnabled',
  'emoticonSeed',
] as const

function setConfigValue(key: string, value: string, quiet = false): void {
  if (!configurableKeys.includes(key as typeof configurableKeys[number])) {
    console.log(chalk.red(`无效的配置项: ${key}`))
    console.log(chalk.gray(`可用: ${configurableKeys.join(', ')}`))
    process.exit(1)
  }
  const configValue = key === 'dailySourceCategories' && value.startsWith('base64:')
    ? Buffer.from(value.slice('base64:'.length), 'base64').toString('utf8')
    : value
  configService.set(key as any, configValue)
  if (!quiet) console.log(chalk.green(`✓ 已设置 ${key}`))
}

function ensureConfigurableKey(key: string, json: boolean): void {
  if (configurableKeys.includes(key as typeof configurableKeys[number])) return
  if (json) console.log(JSON.stringify({ success: false, code: 'INVALID_CONFIG_KEY', error: '配置项不可写', key }))
  else {
    console.log(chalk.red(`无效的配置项: ${key}`))
    console.log(chalk.gray(`可用: ${configurableKeys.join(', ')}`))
  }
  process.exit(1)
}

configCmd
  .command('show')
  .description('显示当前配置')
  .option('--json', '输出脱敏 JSON，不返回路径、账号或密钥')
  .action((opts) => {
    const config = configService.getAll()
    if (opts.json) {
      let sourceCategoryCount = 0
      try {
        const categories = JSON.parse(String(configService.get('dailySourceCategories') || '{}'))
        sourceCategoryCount = categories && typeof categories === 'object' && !Array.isArray(categories)
          ? Object.keys(categories).length
          : 0
      } catch {}
      const dailySources = String(configService.get('dailySources') || '')
        .split(/[,;\n]+/)
        .map(value => value.trim())
        .filter(Boolean)
      console.log(JSON.stringify({
        success: true,
        schema: 'weflow-config-status/v1',
        initialized: configService.isConfigured(),
        dataVersion: config.dataVersion || null,
        databases: {
          legacyConfigured: !!config.dbPath3x && !!config.decryptKey3x,
          messageConfigured: !!config.ntDbPath && !!config.ntKey,
          contactsConfigured: !!config.contactDbPath && !!config.contactKey,
          momentsConfigured: !!configService.get('snsDbPath') && !!configService.get('snsKey'),
          favoritesConfigured: !!configService.get('favDbPath') && (!!configService.get('favKey') || !!configService.get('favPassphrase')),
        },
        ai: {
          engine: configService.get('aiEngine') || 'deepseek',
          configured: ['ollama', 'lmstudio'].includes(String(configService.get('aiEngine') || 'deepseek')) || !!configService.get('deepseekApiKey'),
          dailyEnabled: configService.get('dailyAiEnabled') !== 'false',
        },
        daily: {
          restrictedSources: dailySources.length > 0,
          sourceCount: dailySources.length,
          categorizedSourceCount: sourceCategoryCount,
        },
        assistant: {
          privacyMode: configService.get('assistantPrivacy') || 'strict',
          directWhitelistConfigured: !!String(configService.get('assistantWhitelist') || '').trim(),
          groupWhitelistConfigured: !!String(configService.get('assistantGroupWhitelist') || '').trim(),
          groupMentionRequired: configService.get('assistantGroupRequireMention') !== 'false',
        },
      }, null, 2))
      return
    }
    console.log(chalk.cyan('当前配置:\n'))
    console.log(`数据版本: ${config.dataVersion || chalk.gray('(自动检测)')}`)
    console.log(`4.x 数据目录: ${config.dbPath || chalk.gray('(未设置)')}`)
    console.log(`4.x 密钥: ${config.decryptKey ? '已设置' : chalk.gray('(未设置)')}`)
    console.log(`3.x 数据库: ${config.dbPath3x || chalk.gray('(未设置)')}`)
    console.log(`3.x 密钥: ${config.decryptKey3x ? '已设置' : chalk.gray('(未设置)')}`)
    console.log(`NT 数据库: ${config.ntDbPath || chalk.gray('(未设置)')}`)
    console.log(`NT 密钥: ${config.ntKey ? '已设置' : chalk.gray('(未设置)')}`)
    console.log(`联系人DB: ${config.contactDbPath || chalk.gray('(未设置)')}`)
    console.log(`联系人DB密钥: ${config.contactKey ? '已设置' : chalk.gray('(未设置)')}`)
    console.log(`微信账号: ${config.wxid || chalk.gray('(未设置)')}`)
    console.log(`公众号日报来源: ${configService.get('dailySources') || chalk.gray('(全部公众号)')}`)
    console.log(`公众号日报 AI: ${configService.get('dailyAiEnabled') === 'false' ? '已关闭' : '已开启'}`)
    console.log(`公众号日报排除主题: ${configService.get('dailyExcludeTopics') || chalk.gray('(不排除)')}`)
  })

configCmd
  .command('set <key> <value>')
  .description('设置配置项')
  .option('--dry-run', '仅预览，不修改配置')
  .option('--yes', '确认执行')
  .option('--json', '输出 JSON 格式，不返回配置值')
  .action((key: string, value: string, opts) => {
    ensureConfigurableKey(key, !!opts.json)
    const preview = { action: 'config.set', key, source: 'argument' }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan(`将设置配置项 ${key}`))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认修改配置', ...preview }))
      process.exit(1)
    }
    setConfigValue(key, value, !!opts.json)
    if (opts.json) console.log(JSON.stringify({ success: true, changed: true, ...preview }))
  })

configCmd
  .command('set-env <key> <environment>')
  .description('从环境变量读取值并保存，避免秘密出现在命令参数中')
  .option('--dry-run', '仅预览，不修改配置')
  .option('--yes', '确认执行')
  .option('--json', '输出 JSON 格式，不返回环境变量值')
  .action((key: string, environment: string, opts) => {
    ensureConfigurableKey(key, !!opts.json)
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(environment)) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_ENVIRONMENT_NAME', error: '环境变量名称无效' }))
      else console.log(chalk.red('环境变量名称无效'))
      process.exit(1)
    }
    const value = process.env[environment]
    if (!value) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'ENVIRONMENT_VALUE_MISSING', error: `环境变量 ${environment} 未设置或为空` }))
      else console.log(chalk.red(`环境变量 ${environment} 未设置或为空`))
      process.exit(1)
    }
    const preview = { action: 'config.set-env', key, environment }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan(`将从环境变量 ${environment} 设置配置项 ${key}`))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认修改配置', ...preview }))
      process.exit(1)
    }
    setConfigValue(key, value, !!opts.json)
    if (opts.json) console.log(JSON.stringify({ success: true, changed: true, ...preview }))
  })

configCmd
  .command('clear')
  .description('清除所有配置')
  .option('--dry-run', '仅预览，不清除配置')
  .option('--yes', '确认清除全部配置')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    if (opts.dryRun) {
      const preview = { success: true, dryRun: true, action: 'config.clear', hasConfiguration: Object.keys(configService.getAll()).length > 0 }
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan('配置清除预览：将删除数据库访问、AI、日报、访问控制和助手设置。'))
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认清除全部配置' }))
        process.exit(1)
      }
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: '将清除数据库访问、AI、日报、白名单和机器人等全部配置。继续吗？',
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消，未修改配置。'))
        return
      }
    }
    configService.clear()
    if (opts.json) {
      console.log(JSON.stringify({ success: true, action: 'config.clear' }))
      return
    }
    console.log(chalk.green('✓ 配置已清除'))
  })

configCmd
  .command('forget-keys')
  .description('仅清除本机数据库访问密钥，用于重新初始化测试')
  .option('--dry-run', '仅预览，不清除数据库访问密钥')
  .option('--yes', '跳过确认')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    if (opts.dryRun) {
      const preview = { success: true, dryRun: true, action: 'config.forget-keys', preservesNonDatabaseSettings: true }
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan('数据库密钥清除预览：保留数据目录、AI、日报、访问控制和助手设置。'))
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认清除数据库访问密钥' }))
        process.exit(1)
      }
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: '清除数据库访问密钥后，聊天读取会暂时不可用。继续吗？',
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消，未修改配置。'))
        return
      }
    }

    configService.clearDatabaseKeys()
    if (opts.json) {
      console.log(JSON.stringify({ success: true, action: 'config.forget-keys' }))
      return
    }
    console.log(chalk.green('✓ 已清除数据库访问密钥。'))
    console.log(chalk.gray('  数据目录、AI、日报、白名单和机器人设置均已保留。'))
    console.log(chalk.gray('  现在可运行 weflow-cli init 测试首次初始化流程。'))
  })

// ==================== sessions ====================
program
  .command('sessions')
  .description('查看会话列表')
  .option('-k, --keyword <keyword>', '搜索关键词')
  .option('-n, --limit <number>', '最大数量', '30')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    const limit = parseCliInteger(opts.limit, 'limit', 1, 1000, !!opts.json)
    if (!configService.isConfigured()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, error: '未完成初始化' })); process.exit(1) }
      console.log(chalk.red('请先运行 weflow-cli init'))
      process.exit(1)
    }

    const sessions = await chatService.listSessions(opts.keyword, limit)
    if (sessions.length === 0) {
      if (opts.json) { console.log(JSON.stringify({ success: true, sessions: [] })); return }
      console.log(chalk.gray('未找到会话'))
      return
    }

    if (opts.json) {
      console.log(JSON.stringify({ success: true, sessions }, null, 2))
      return
    }
    console.log(chalk.cyan(`会话列表 (${sessions.length} 条):\n`))
    console.log(chalk.gray('序号  会话ID                昵称            最后消息'))
    console.log(chalk.gray('─'.repeat(70)))

    for (let i = 0; i < sessions.length; i++) {
      const s = sessions[i]
      const num = String(i + 1).padStart(3)
      const id = (s.username || '').padEnd(20)
      const name = (s.displayName || '').slice(0, 12).padEnd(12)
      const summary = (s.summary || '').slice(0, 30)
      console.log(`${num}  ${id} ${name} ${summary}`)
    }
  })

// ==================== messages ====================
program
  .command('messages <talker>')
  .description('查看指定会话的消息 (支持 wxid / 昵称 / 备注 / 序号)')
  .option('-n, --limit <number>', '最大数量', '50')
  .option('-o, --offset <number>', '偏移量', '0')
  .option('-s, --start <timestamp>', '开始时间戳')
  .option('-e, --end <timestamp>', '结束时间戳')
  .option('--json', '输出 JSON 格式')
  .option('--non-interactive', '禁止交互选择；匹配不唯一时返回错误')
  .action(async (talkerInput: string, opts) => {
    const limit = parseCliInteger(opts.limit, 'limit', 1, 5000, !!opts.json)
    const offset = parseCliInteger(opts.offset, 'offset', 0, 1_000_000, !!opts.json)
    const start = opts.start === undefined ? undefined : parseCliInteger(opts.start, 'start', 0, Number.MAX_SAFE_INTEGER, !!opts.json)
    const end = opts.end === undefined ? undefined : parseCliInteger(opts.end, 'end', 0, Number.MAX_SAFE_INTEGER, !!opts.json)
    if (start !== undefined && end !== undefined && start > end) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_ARGUMENT', field: 'start', error: 'start 不能晚于 end' }))
      else console.log(chalk.red('start 不能晚于 end'))
      process.exit(1)
    }
    if (!configService.isConfigured()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, error: '未完成初始化' })); process.exit(1) }
      console.log(chalk.red('\n❌ 还没配置'))
      console.log(chalk.gray('  运行: weflow-cli init\n'))
      process.exit(1)
    }

    const talker = await resolveTalker(talkerInput, opts.json, opts.nonInteractive || opts.json)

    const messages = start !== undefined || end !== undefined
      ? (await chatService.getMessagesInRange(talker, limit + offset, start, end)).slice(offset, offset + limit)
      : await chatService.getMessages(talker, limit, offset)

    if (messages.length === 0) {
      if (opts.json) { console.log(JSON.stringify({ success: true, talker, messages: [] })); return }
      console.log(chalk.gray('未找到消息'))
      return
    }

    if (opts.json) {
      console.log(JSON.stringify({ success: true, talker, messages }, null, 2))
      return
    }
    console.log(chalk.cyan(`消息记录 - ${talker} (${messages.length} 条):\n`))

    for (const m of messages) {
      const time = new Date(m.createTime * 1000).toLocaleString('zh-CN')
      const senderName = m.isSend ? chalk.green('我') : chalk.blue((m as any).senderDisplay || m.senderUsername || talker)
      const content = (m.parsedContent || m.rawContent || '').replace(/\n/g, ' ').slice(0, 80)
      console.log(chalk.gray(`[${time}]`) + ` ${senderName}: ${content}`)
    }
  })

// ==================== sync ====================
// 增量同步检查点。契约见 docs/SYNC_CONTRACT.md, 边界见 D-029。
// 不宣称「稳定游标」: D-027 要求它在所有后端支持前不得对外宣称,
// 这里只做重叠时间窗 + 本地去重。
const syncCmd = program.command('sync').description('本地消息同步检查点（重叠窗口读取与覆盖报告）')

syncCmd
  .command('run <talker>')
  .description('执行一次同步并写入检查点')
  .option('--since <date>', '起始日期或 ISO 时间（首次运行需要）')
  .option('--full', '读取全部历史（首次运行需要；耗时可能较长）')
  .option('--overlap <seconds>', '重叠窗口秒数', '300')
  .option('-n, --limit <number>', '本次最多读取条数（0=不限）', '0')
  .option('--dry-run', '仅预览，不读取消息、不写状态')
  .option('--yes', '确认执行')
  .option('--json', '输出机器可读结果')
  .option('--non-interactive', '禁止交互确认')
  .action(async (talker: string, opts) => {
    const overlap = parseCliInteger(opts.overlap, 'overlap', 0, 86400, !!opts.json)
    const limit = parseCliInteger(opts.limit, 'limit', 0, 1_000_000, !!opts.json)

    let since: number | undefined
    if (opts.since) {
      try {
        since = parseLocalDateOrIso(opts.since)
      } catch (error) {
        const message = error instanceof DateRangeError ? error.message : '日期格式无效'
        if (opts.json) { console.log(JSON.stringify({ success: false, code: 'INVALID_DATE', error: message })); process.exit(1) }
        console.error(chalk.red(`✗ ${message}`))
        process.exit(1)
      }
    }
    if (opts.full && since !== undefined) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'INVALID_ARGUMENT', error: '--since 与 --full 不能同时使用' })); process.exit(1) }
      console.error(chalk.red('✗ --since 与 --full 不能同时使用'))
      process.exit(1)
    }

    // Preview deliberately does not resolve the talker: that reads the
    // database, and a preview must not touch local data (D-025).
    const prior = (() => {
      try { return syncStateStore.read(SYNC_SOURCE, talker) } catch { return null }
    })()
    const preview = {
      success: true,
      dryRun: true,
      action: 'sync.run',
      schema: SYNC_SCHEMA,
      source: SYNC_SOURCE,
      talkerResolved: false,
      hasCheckpoint: !!prior,
      window: {
        from: since ?? prior?.checkpoint.newestCreateTime ?? null,
        overlapSeconds: overlap,
        full: !!opts.full,
      },
      estimatedRecords: prior?.recordsRead ?? null,
      readsLocalChat: true,
      writesLocalState: true,
      stableCursor: false,
    }

    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else {
        console.log(chalk.cyan('\n同步预览'))
        console.log(chalk.gray(`  范围: ${opts.full ? '全部历史' : since ? new Date(since * 1000).toLocaleString() : '上次检查点'}`))
        console.log(chalk.gray(`  重叠窗口: ${overlap} 秒`))
        console.log(chalk.gray('  将读取本地聊天记录并写入同步检查点，不调用 AI。\n'))
      }
      return
    }

    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
        process.exit(1)
      }
      if (opts.nonInteractive) {
        console.error(chalk.red('✗ 非交互模式需要 --yes'))
        process.exit(1)
      }
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: opts.full
          ? '将读取该会话的全部历史消息，可能耗时数分钟。继续吗？'
          : '将读取指定范围内的消息并写入本地同步检查点。继续吗？',
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消'))
        return
      }
    }

    if (!configService.isConfigured()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' })); process.exit(1) }
      console.error(chalk.red('✗ 未完成初始化，请先运行 weflow-cli init'))
      process.exit(1)
    }

    let resolvedTalker = ''
    try {
      resolvedTalker = await resolveTalkerCore(talker, { interactive: !opts.nonInteractive })
    } catch {
      resolvedTalker = ''
    }
    if (!resolvedTalker) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'INVALID_ARGUMENT', error: '未找到该会话' })); process.exit(1) }
      console.error(chalk.red('✗ 未找到该会话'))
      process.exit(1)
    }
    // A display name is nicety only; the state is keyed by the resolved id.
    let displayName = resolvedTalker
    try {
      const match = (await chatService.listSessions()).find(s => s.username === resolvedTalker)
      if (match?.displayName && match.displayName !== resolvedTalker) displayName = match.displayName
    } catch { /* keep the id */ }

    try {
      const result = await runSync(resolvedTalker, {
        since, full: !!opts.full, overlapSeconds: overlap, limit,
        scope: displayName,
        read: (who, howMany, from) => chatService.getMessagesWithShards(who, howMany, 0, from),
      })
      if (opts.json) {
        console.log(JSON.stringify({
          success: result.success,
          ...(result.code ? { code: result.code } : {}),
          action: result.action,
          schema: SYNC_SCHEMA,
          source: SYNC_SOURCE,
          scope: result.scope,
          talker: resolvedTalker,
          window: result.window,
          recordsRead: result.recordsRead,
          recordsDeduplicated: result.recordsDeduplicated,
          recordsReturned: result.recordsReturned,
          shards: result.shards,
          coverage: result.coverage,
          mayHaveMore: result.mayHaveMore,
          partial: result.partial,
          warnings: result.warnings,
          jobId: result.jobId,
        }))
        if (!result.success) process.exit(1)
        return
      }
      const mark = result.coverage === 'complete' ? '✓' : result.coverage === 'unverified' ? '!' : '✗'
      console.log(chalk.green(`\n${mark} 同步完成: ${result.recordsReturned}/${result.recordsRead} 条（去重 ${result.recordsDeduplicated}）`))
      console.log(chalk.gray(`  覆盖: ${result.coverage}  ·  分片: ${result.shards?.opened ?? '?'} 读 / ${result.shards?.failed ?? 0} 失败`))
      for (const warning of result.warnings) console.log(chalk.yellow(`  ⚠ ${warning}`))
      if (result.partial) {
        console.log(chalk.yellow('  本次为部分完成，未推进成功时间；用 sync status 查看详情。'))
      }
      console.log('')
      if (!result.success) process.exit(1)
    } catch (error: any) {
      if (error instanceof SyncRangeRequiredError) {
        if (opts.json) { console.log(JSON.stringify({ success: false, code: 'SYNC_RANGE_REQUIRED', error: error.message })); process.exit(1) }
        console.error(chalk.red(`✗ ${error.message}`))
        process.exit(1)
      }
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'SYNC_FAILED', error: '同步失败' })); process.exit(1) }
      console.error(chalk.red(`✗ 同步失败: ${error?.message || error}`))
      process.exit(1)
    }
  })

syncCmd
  .command('status [talker]')
  .description('查看同步检查点与最近任务')
  .option('--json', '输出 JSON 格式')
  .action(async (talker: string | undefined, opts) => {
    // No database access: this has to work while the database is locked.
    let states: any[] = []
    try { states = syncStateStore.list() } catch { states = [] }
    const lastJob = syncStateStore.recentJobs(1)[0] ?? null

    if (talker) {
      const match = states.find(s => s.talker === talker || s.scope === talker)
      if (!match) {
        if (opts.json) { console.log(JSON.stringify({ success: false, code: 'SYNC_STATE_NOT_FOUND', error: '没有该会话的同步记录' })); process.exit(1) }
        console.error(chalk.red('✗ 没有该会话的同步记录'))
        process.exit(1)
      }
      if (opts.json) { console.log(JSON.stringify({ success: true, schema: SYNC_SCHEMA, state: match, lastJob })); return }
      console.log(chalk.cyan(`\n同步记录 — ${match.scope}`))
      console.log(chalk.gray(`  覆盖: ${match.coveredFrom || '—'} ~ ${match.coveredTo || '—'}`))
      console.log(chalk.gray(`  可信度: ${match.coverage}  ·  分片 ${match.shardsRead} 读 / ${match.shardsFailed} 失败`))
      console.log(chalk.gray(`  上次成功: ${match.lastSuccessfulRun || '—'}  ·  上次尝试: ${match.lastAttempt || '—'}\n`))
      return
    }

    if (opts.json) {
      console.log(JSON.stringify({
        success: true,
        schema: SYNC_SCHEMA,
        stableCursor: false,
        scopes: states.map(s => ({
          source: s.source, scope: s.scope, talker: s.talker,
          lastSuccessfulRun: s.lastSuccessfulRun, lastAttempt: s.lastAttempt,
          coveredFrom: s.coveredFrom, coveredTo: s.coveredTo,
          recordsRead: s.recordsRead, recordsDeduplicated: s.recordsDeduplicated,
          coverage: s.coverage, shardsRead: s.shardsRead, shardsFailed: s.shardsFailed,
          mayHaveMore: s.mayHaveMore, warnings: s.warnings,
        })),
        lastJob,
      }))
      return
    }
    if (!states.length) {
      console.log(chalk.gray('\n还没有任何同步记录。首次运行: weflow-cli sync run <会话> --since <日期>\n'))
      return
    }
    console.log(chalk.cyan(`\n同步记录 (${states.length} 个会话):\n`))
    for (const s of states) {
      const mark = s.coverage === 'complete' ? '✓' : s.coverage === 'unverified' ? '!' : '✗'
      console.log(`  ${mark} ${s.scope}  ${chalk.gray(`${s.coveredFrom || '—'} ~ ${s.coveredTo || '—'}  分片 ${s.shardsRead}/${s.shardsFailed} 失败`)}`)
    }
    console.log('')
  })

syncCmd
  .command('verify <talker>')
  .description('核对检查点与当前数据的边界')
  .option('--json', '输出 JSON 格式')
  .action(async (talker: string, opts) => {
    // Read-only: it writes nothing, so it needs no confirmation - the same
    // posture as `messages`.
    if (!configService.isConfigured()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' })); process.exit(1) }
      console.error(chalk.red('✗ 未完成初始化'))
      process.exit(1)
    }
    let state: any = null
    try { state = syncStateStore.read(SYNC_SOURCE, talker) } catch { state = null }
    if (!state) state = syncStateStore.list().find(s => s.scope === talker) ?? null
    if (!state) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'SYNC_STATE_NOT_FOUND', error: '没有该会话的同步记录' })); process.exit(1) }
      console.error(chalk.red('✗ 没有该会话的同步记录'))
      process.exit(1)
    }

    const checks: Array<Record<string, unknown>> = [{ name: 'checkpoint-present', ok: true }]

    // Re-read and compare. This is the count summary of §4.4 in the absence
    // of a local index.
    const read = await chatService.getMessagesWithShards(state.talker, 0, 0)
    const fromSeconds = state.coveredFrom ? Math.floor(Date.parse(state.coveredFrom) / 1000) : null
    const inWindow = fromSeconds === null
      ? read.messages
      : read.messages.filter(m => Number(m.createTime) >= fromSeconds)
    checks.push({ name: 'window-re-readable', ok: inWindow.length > 0, returned: inWindow.length })

    const newest = read.messages.length
      ? Math.max(...read.messages.map(m => Number(m.createTime) || 0))
      : null
    checks.push({
      name: 'newest-message-present',
      ok: newest !== null && newest >= (state.checkpoint?.newestCreateTime ?? 0),
      recorded: state.checkpoint?.newestCreateTime ?? null,
      observed: newest,
    })

    if (read.shards) {
      checks.push({ name: 'shards-readable', ok: read.shards.failed === 0,
                    scanned: read.shards.scanned, failed: read.shards.failed })
    } else {
      // Neither a pass nor a failure: this backend simply cannot say.
      checks.push({ name: 'shards-readable', ok: true, skipped: true,
                    reason: 'backend-does-not-report-shards' })
    }

    const success = checks.every(c => c.ok || c.skipped)
    if (opts.json) {
      console.log(JSON.stringify({ success, schema: SYNC_SCHEMA, scope: state.scope, coverage: state.coverage, checks }))
      if (!success) process.exit(1)
      return
    }
    console.log(chalk.cyan(`\n核对 — ${state.scope}\n`))
    for (const check of checks) {
      const mark = check.skipped ? '–' : check.ok ? '✓' : '✗'
      console.log(`  ${mark} ${check.name}${check.skipped ? chalk.gray(` (跳过: ${check.reason})`) : ''}`)
    }
    console.log('')
    if (!success) process.exit(1)
  })

// ==================== contacts ====================
program
  .command('contacts')
  .description('查看联系人列表')
  .option('-k, --keyword <keyword>', '搜索关键词 (自动扩大搜索范围)')
  .option('-n, --limit <number>', '最大数量', '500')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    const limit = parseCliInteger(opts.limit, 'limit', 1, 5000, !!opts.json)
    if (!configService.isConfigured()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, error: '未完成初始化' })); process.exit(1) }
      console.log(chalk.red('请先运行 weflow-cli init'))
      process.exit(1)
    }

    const contacts = await chatService.listContacts(opts.keyword, limit)
    if (contacts.length === 0) {
      if (opts.json) { console.log(JSON.stringify({ success: true, contacts: [] })); return }
      console.log(chalk.gray('未找到联系人'))
      return
    }

    if (opts.json) {
      console.log(JSON.stringify({ success: true, contacts }, null, 2))
      return
    }
    console.log(chalk.cyan(`联系人列表 (${contacts.length} 条):\n`))
    console.log(chalk.gray('序号  用户ID                昵称/备注'))
    console.log(chalk.gray('─'.repeat(60)))

    for (let i = 0; i < contacts.length; i++) {
      const c = contacts[i]
      const num = String(i + 1).padStart(3)
      const id = (c.username || '').padEnd(20)
      const name = c.remark || c.displayName || c.nickname || ''
      console.log(`${num}  ${id} ${name}`)
    }
  })

// ==================== export ====================
program
  .command('export <talker> <format>')
  .description('导出聊天记录 (支持 wxid / 昵称 / 备注 / 序号)')
  .option('-o, --output <dir>', '输出目录', './output')
  .option('-n, --limit <number>', '最大数量（0=全量导出）', '0')
  .option('-d, --date <YYYY-MM-DD>', '仅导出指定日期的消息（本地时间）')
  .option('--from <date>', '起始日期或 ISO 时间')
  .option('--to <date>', '结束日期或 ISO 时间')
  .option('--contract <name>', 'JSON 数据契约：raw 或 weflow-v1', 'raw')
  .option('--json', '输出机器可读的导出结果，不改变导出文件格式')
  .option('--non-interactive', '禁止交互选择；匹配不唯一时返回错误')
  .option('--full-images', '使用原图而非微信缓存缩略图（更清晰，但导出慢数倍）')
  .action(async (talkerInput: string, format: string, opts) => {
    const validFormats = ['json', 'txt', 'html', 'excel']
    if (!validFormats.includes(format)) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_FORMAT', error: '导出格式无效', supported: validFormats }))
      else {
        console.log(chalk.red(`无效格式: ${format}`))
        console.log(chalk.gray(`可用: ${validFormats.join(', ')}`))
      }
      process.exit(1)
    }
    const limit = parseCliInteger(opts.limit, 'limit', 0, 1_000_000, !!opts.json)
    if (!configService.isConfigured()) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' }))
      else {
        console.log(chalk.red('\n❌ 还没配置'))
        console.log(chalk.gray('  运行: weflow-cli init\n'))
      }
      process.exit(1)
    }

    const talker = await resolveTalker(talkerInput, !!opts.json, opts.nonInteractive || opts.json)

    if (!opts.json) console.log(chalk.cyan(`正在导出 ${talker} 的聊天记录 (${format})...\n`))

    let result
    let from: number | undefined
    let to: number | undefined
    try {
      const range = resolveExportDateRange({
        date: opts.date,
        from: opts.from,
        to: opts.to,
        preserveDateForRichHtml: format === 'html',
      })
      from = range.from
      to = range.to
    } catch (error) {
      const dateError = error instanceof DateRangeError ? error : new DateRangeError('INVALID_DATE', '导出日期无效')
      if (opts.json) console.log(JSON.stringify({ success: false, code: dateError.code, error: dateError.message }))
      else console.log(chalk.red(dateError.message))
      process.exit(1)
    }
    if (!['raw', 'weflow-v1'].includes(opts.contract)) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_CONTRACT', error: '数据契约无效', supported: ['raw', 'weflow-v1'] }))
      else {
        console.log(chalk.red(`不支持的数据契约: ${opts.contract}`))
        console.log(chalk.gray('可用: raw, weflow-v1'))
      }
      process.exit(1)
    }
    switch (format) {
      case 'json':
        result = await exportService.exportJson(talker, opts.output, limit, from, to, opts.contract)
        break
      case 'txt':
        result = await exportService.exportTxt(talker, opts.output, limit, from, to)
        break
      case 'html':
        result = await exportService.exportHtml(talker, opts.output, limit, opts.date || '', from, to, !!opts.json, { fullImages: opts.fullImages === true })
        break
      case 'excel':
        result = await exportService.exportExcel(talker, opts.output, limit, from, to)
        break
    }

    if (result?.success) {
      if (opts.json) console.log(JSON.stringify({ success: true, format, contract: format === 'json' ? opts.contract : null, path: result.path, count: result.count ?? null }, null, 2))
      else console.log(chalk.green(`✓ 导出成功: ${result.path}`))
    } else {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'EXPORT_FAILED', error: result?.error || '导出失败' }))
      else console.log(chalk.red(`✗ 导出失败: ${result?.error}`))
      process.exit(1)
    }
  })

// ==================== evidence ====================
program
  .command('evidence <talker>')
  .description('整理本地聊天证据包（原始消息、哈希与保全说明）')
  .option('-o, --output <dir>', '证据包输出目录', './output/evidence')
  .option('-n, --limit <number>', '最多包含消息数', '10000')
  .option('--case <note>', '案件或争议说明（仅保存在本地证据包）')
  .option('--json', '输出 JSON 格式')
  .option('--non-interactive', '禁止交互选择；匹配不唯一时返回错误')
  .action(async (talkerInput: string, opts) => {
    if (!configService.isConfigured()) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' }))
        process.exit(1)
      }
      console.log(chalk.red('\n❌ 还没配置'))
      console.log(chalk.gray('  运行: weflow-cli init\n'))
      process.exit(1)
    }
    const talker = await resolveTalker(talkerInput, opts.json, opts.nonInteractive || opts.json)
    const limit = parseCliInteger(opts.limit, 'limit', 1, 100000, opts.json)
    const messages = await chatService.getMessages(talker, limit)
    if (messages.length === 0) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'NO_MESSAGES', error: '未找到消息' })); return }
      console.log(chalk.gray('未找到消息，未创建证据包'))
      return
    }
    const result = writeEvidencePackage(opts.output, talker, messages, opts.case)
    if (opts.json) {
      console.log(JSON.stringify({ success: true, path: result.path, manifest: result.manifest }, null, 2))
      return
    }
    console.log(chalk.green(`✓ 证据包已创建: ${result.path}`))
    console.log(chalk.gray(`  消息: ${result.manifest.messageCount} 条`))
    console.log(chalk.gray(`  SHA-256: ${result.manifest.messagesSha256}`))
    console.log(chalk.yellow('  注意: 哈希用于证明文件未被改动，不等于证明内容真实或必然具有法律效力。'))
  })

program
  .command('evidence-review <talker>')
  .description('用 AI 整理聊天中的法律争议线索（不提供法律结论）')
  .option('-o, --output <dir>', '分析结果输出目录', './output/evidence-review')
  .option('-n, --limit <number>', '最多分析消息数', '500')
  .option('--allow-cloud', '明确允许将按隐私模式处理后的内容发送到云端 AI')
  .option('--dry-run', '仅预览，不读取聊天、调用 AI 或写入分析结果')
  .option('--yes', '确认读取聊天并生成分析结果')
  .option('--json', '输出机器可读结果，不返回分析正文')
  .action(async (talkerInput: string, opts) => {
    const limit = parseCliInteger(opts.limit, 'limit', 1, 5000, !!opts.json)
    const preview = {
      success: true,
      dryRun: true,
      action: 'evidence-review',
      limit,
      readsLocalChat: true,
      usesAi: true,
      cloudAllowed: Boolean(opts.allowCloud),
      writesLocalResult: true,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`预览：将读取最多 ${limit} 条指定会话消息，调用${opts.allowCloud ? '已授权的云端或本地' : '本地'}模型并写入分析结果。`))
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
        process.exit(1)
      }
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: `确认读取指定会话并使用${opts.allowCloud ? '已授权的云端或本地' : '本地'}模型生成争议线索分析吗？`,
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消'))
        return
      }
    }
    if (!configService.isConfigured()) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' }))
      else {
        console.log(chalk.red('\n❌ 还没配置'))
        console.log(chalk.gray('  运行: weflow-cli init\n'))
      }
      process.exit(1)
    }
    const talker = await resolveTalker(talkerInput, !!opts.json, !!opts.json)
    const messages = await chatService.getMessages(talker, limit)
    if (messages.length === 0) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'NO_MESSAGES', error: '未找到消息，未创建分析结果' }))
      else console.log(chalk.gray('未找到消息，未创建分析结果'))
      return
    }
    const { AssistantService } = await import('../src/services/assistantService.js')
    const result = await new AssistantService().reviewEvidence(talker, messages, Boolean(opts.allowCloud))
    const { mkdirSync } = await import('fs')
    mkdirSync(opts.output, { recursive: true })
    const outputPath = join(opts.output, `${Date.now()}-${talker.replace(/[^a-zA-Z0-9._-]+/g, '_').slice(0, 60)}.md`)
    writeFileSync(outputPath, [
      '# 聊天法律争议线索整理', '',
      `- 会话：${talker}`,
      `- 消息数：${messages.length}`,
      `- 推理方式：${result.localInference ? '本地模型' : '云端模型（已按隐私模式处理）'}`,
      `- 脱敏次数：${result.redactions}`,
      '', result.text, '',
      '---', '',
      '本文件仅供线索整理，不是法律意见，不代表违法认定或法院必然采信。请保留原设备、原始数据和完整上下文，并咨询专业人士。',
    ].join('\n'), 'utf8')
    if (opts.json) {
      console.log(JSON.stringify({
        success: true,
        action: 'evidence-review',
        count: messages.length,
        outputCreated: true,
        localInference: result.localInference,
        redactions: result.redactions,
        cloudAllowed: Boolean(opts.allowCloud),
      }, null, 2))
      return
    }
    console.log(chalk.green(`✓ 分析结果已保存: ${outputPath}`))
    console.log(chalk.yellow(`  ${result.localInference ? '使用本地模型，聊天正文未离开本机' : '已使用云端模型，请确认隐私模式和授权范围'}`))
  })

// ==================== dbkey ====================
program
  .command('dbkey')
  .description('从运行中的微信进程提取数据库解密密钥 (自动检测版本)')
  .option('-t, --timeout <ms>', '超时时间(毫秒)', '60000')
  .option('--force', '即使已有本地配置也执行捕获')
  .option('--dry-run', '仅预览，不扫描进程内存或捕获密钥')
  .option('--yes', '确认启动人工密钥捕获流程')
  .option('--json', '输出机器可读预览；实际捕获必须在交互终端执行')
  .action(async (opts) => {
    const configured = configService.isConfigured()
    const preview = {
      success: true,
      dryRun: true,
      action: 'dbkey.capture',
      interactiveRequired: true,
      scansProcessMemory: true,
      captureRequired: Boolean(opts.force || !configured),
      writesConfiguration: false,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`密钥捕获预览：${preview.captureRequired ? '将扫描运行中的微信进程' : '已有本地配置，默认不会重复捕获'}`))
      return
    }
    if (!opts.force && configured) {
      if (opts.json) {
        console.log(JSON.stringify({ success: true, action: 'dbkey.reuse', captureSkipped: true }))
        return
      }
      console.log(chalk.cyan('🔑 提取微信数据库密钥\n'))
      console.log(chalk.green('✓ 已检测到本地数据库访问配置，未重复捕获密钥。'))
      console.log(chalk.gray('  可直接运行 weflow-cli sessions 验证访问。'))
      console.log(chalk.gray('  仅在需要排障时使用 --force。'))
      return
    }
    if (opts.json) {
      console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'INTERACTIVE_REQUIRED' }))
      process.exit(1)
    }
    if (!opts.yes) {
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: '确认启动需要人工配合的数据库密钥捕获流程吗？',
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消'))
        return
      }
    }
    console.log(chalk.cyan('🔑 提取微信数据库密钥\n'))
    console.log(chalk.gray('请确保微信已登录且正在运行。\n'))

    if (process.platform === 'linux') {
      // Linux：/proc 内存扫描提取 NT 密钥
      const { NtCore } = await import('../src/core/ntCore.js')
      const result = await NtCore.scan()
      if (result.success && result.matched && result.matched.length > 0) {
        for (const db of result.matched) {
          console.log(chalk.green(`✓ ${db.name} (${(db.size / 1024 / 1024).toFixed(1)}MB)`))
          console.log(chalk.white('  密钥: 已匹配，未显示'))
          console.log(chalk.white('  盐值: 已匹配，未显示'))
        }
        console.log(chalk.gray('\n提示: 运行 weflow-cli init 可自动写入配置'))
      } else if (result.error?.includes('PERMISSION_DENIED')) {
        console.log(chalk.red('✗ 需要 root 或 CAP_SYS_PTRACE 权限'))
        console.log(chalk.gray('  授权: sudo setcap cap_sys_ptrace=ep $(which python3)'))
        process.exit(1)
      } else {
        console.log(chalk.red(`✗ ${result.error || '未匹配到密钥'}`))
        process.exit(1)
      }
      return
    }

    const version = await keyService.detectWeChatVersion()
    if (!version) {
      console.log(chalk.red('未检测到微信进程 (WeChat.exe 或 Weixin.exe)'))
      process.exit(1)
    }
    console.log(chalk.gray(`检测到微信 ${version} 版本\n`))

    if (version === '3.x') {
      const result = await keyService.extract3xKey((msg) => {
        console.log(chalk.gray(`  ${msg}`))
      })
      if (result.success && result.key) {
        console.log(chalk.green('\n✓ 密钥已捕获，未显示'))
        console.log(chalk.green('  账号与消息目录: 已识别，未显示'))
      } else {
        console.log(chalk.red(`\n✗ 失败: ${result.error}`))
        process.exit(1)
      }
    } else {
      const timeout = parseCliInteger(opts.timeout, 'timeout', 1000, 600000)
      const result = await keyService.autoGetDbKey(timeout, (msg) => {
        console.log(chalk.gray(`  ${msg}`))
      })
      if (result.success && result.key) {
        console.log(chalk.green('\n✓ 密钥已捕获，未显示'))
        console.log(chalk.gray('\n提示: 运行 weflow-cli init 可自动派生并写入各库密钥配置'))
      } else {
        console.log(chalk.red(`\n✗ 失败: ${result.error}`))
        process.exit(1)
      }
    }
  })

// ==================== scan ====================
program
  .command('scan')
  .description('扫描微信数据目录中的账号')
  .option('-p, --path <path>', '数据目录路径')
  .option('--json', '输出机器可读摘要，不返回路径、账号标识或昵称')
  .action(async (opts) => {
    const path = opts.path || configService.get('dbPath') || dbPathService.getDefaultPath()
    const wxids = dbPathService.scanWxidCandidates(path)
    if (opts.json) {
      console.log(JSON.stringify({ success: wxids.length > 0, accountCount: wxids.length }))
      return
    }
    console.log(chalk.cyan(`扫描目录: ${path}\n`))
    if (wxids.length === 0) {
      console.log(chalk.gray('未找到账号'))
      return
    }

    console.log(chalk.cyan(`找到 ${wxids.length} 个账号:\n`))
    for (const w of wxids) {
      const time = new Date(w.modifiedTime).toLocaleString('zh-CN')
      const nickname = w.nickname ? ` (${w.nickname})` : ''
      console.log(`  ${w.wxid}${nickname}  ${chalk.gray(time)}`)
    }
  })

// ==================== login-wechat ====================
program
  .command('login-wechat')
  .description('微信扫码登录，获取消息收发权限')
  .option('--base-url <url>', 'ilink 服务地址')
  .option('--dry-run', '仅预览，不请求二维码或修改登录状态')
  .option('--yes', '确认启动人工扫码登录流程')
  .option('--json', '输出机器可读预览；实际登录必须在交互终端执行')
  .action(async (opts) => {
    const token = configService.get('wechatOcToken')
    const preview = {
      success: true,
      dryRun: true,
      action: 'wechat-channel.login',
      interactiveRequired: true,
      replacesExistingSession: !!token,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`登录预览：需要人工扫码${token ? '，成功后将替换现有消息通道登录' : ''}`))
      return
    }
    if (opts.json) {
      console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'INTERACTIVE_REQUIRED' }))
      process.exit(1)
    }
    if (!opts.yes) {
      // stdin 不是终端时（管道、计划任务、会话里的 `!` 前缀执行）inquirer 会抛 ExitPromptError——
      // 屏幕上是一条堆栈而不是一句话。这里把它变成解释：扫码登录必须在真实终端里跑。
      // 与守护进程那条 `--yes` 是同一类毛病：交互确认被塞进一个没人能回答的 stdin。
      let confirmed = false
      try {
        const answer = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: token ? '已有登录状态，确认启动重新登录吗？' : '确认启动人工扫码登录吗？',
          default: false,
        }])
        confirmed = !!answer.confirmed
      } catch (error: any) {
        if (error?.name === 'ExitPromptError' || /force closed the prompt/.test(String(error?.message))) {
          console.log(chalk.yellow('当前环境没有可交互的终端。扫码登录必须在真实终端窗口里执行，'))
          console.log(chalk.yellow('或者加 --yes 跳过这个确认（扫码那一步仍然要人来做）。'))
          process.exit(1)
        }
        throw error
      }
      if (!confirmed) {
        console.log(chalk.gray(token ? '保持当前登录状态' : '已取消'))
        return
      }
    }

    console.log(chalk.cyan('正在获取登录二维码...\n'))
    const service = new WechatMessageService({ baseUrl: opts.baseUrl })

    try {
      const { qrcodeContent } = await service.startLogin()
      console.log(chalk.green('请用微信扫描以下二维码:\n'))

      // Print QR in terminal
      try {
        const QRCode = (await import('qrcode-terminal')).default
        QRCode.generate(qrcodeContent, { small: true })
      } catch {
        console.log(chalk.yellow('(终端二维码显示失败，请安装: npm install qrcode-terminal)'))
      }

      console.log(chalk.cyan('\n等待扫码 (约2分钟有效)...'))
      const session = await service.waitForLogin()

      if (session.status === 'confirmed') {
        console.log(chalk.green('\n✓ 登录成功!'))
        console.log(chalk.gray('  消息通道登录状态已安全保存'))
        // 助手的白名单为空时**拒绝所有人**：登录完对着自己的机器人说话，什么都不会回来。
        // 所以这里必须说清"下一步该干什么"。
        // 只打在人看的这条路上——`--json` 那条路照旧不返回账号标识。
        console.log('')
        console.log(chalk.cyan('  助手默认拒绝所有人（白名单为空）。'))
        // 这里**不**替用户写白名单：登录响应给的是 ilink_user_id，而白名单要的是入站消息里
        // 那个 from_user_id（文档里写成 <@im.wechat ID>），两者是不是同一个值**没有被验证过**
        // ——猜错的后果是"白名单非空、看着配好了、却仍然拒你"。真值在第一条被拒的消息里，
        // 所以让它自己现形（见 assistant log 的 [首次配置] 那行）。
        console.log(chalk.gray('  启用方式：先 assistant start，从你的微信给机器人发一条消息，'))
        console.log(chalk.gray('  日志里会出现一行 ' + chalk.cyan('[首次配置]')
          + chalk.gray(' 带着你自己的发送者 ID 和该执行的命令。')))
        console.log(chalk.gray('  只想先看它「本来会怎么答」而不改行为：')
          + chalk.cyan('weflow-cli config set assistantFastRoute log'))
      } else {
        console.log(chalk.red(`\n✗ 登录失败: ${session.error || '超时'}`))
      }
    } catch (e: any) {
      console.log(chalk.red(`\n✗ 登录失败: ${e.message}`))
    }
  })

// ==================== logout-wechat ====================
program
  .command('logout-wechat')
  .description('退出微信消息通道登录')
  .option('--dry-run', '仅预览，不清除登录状态')
  .option('--yes', '确认退出并清除消息通道状态')
  .option('--json', '输出机器可读结果')
  .action(async (opts) => {
    const loggedIn = !!configService.get('wechatOcToken')
    const preview = {
      success: true,
      dryRun: true,
      action: 'wechat-channel.logout',
      loggedIn,
      clearsContextTokens: true,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`登出预览：${loggedIn ? '将清除消息通道登录和会话令牌' : '当前未登录，执行后状态不变'}`))
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
        process.exit(1)
      }
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: '确认退出消息通道并清除本地会话令牌？',
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消'))
        return
      }
    }
    configService.set('wechatOcToken', '')
    configService.set('wechatOcAccountId', '')
    configService.set('wechatOcSyncBuf', '')
    // 登出后历史 context_token 失效, 一并清空
    configService.setContextTokens({})
    if (opts.json) console.log(JSON.stringify({ success: true, action: 'wechat-channel.logout', changed: loggedIn }))
    else console.log(chalk.green('✓ 已退出登录'))
  })

// ==================== whitelist ====================
const whitelistCmd = program
  .command('whitelist')
  .description('白名单管理 (不传参数显示列表)')

whitelistCmd
  .action(() => {
    const entries = whitelistService.getWhitelistEntries()
    if (entries.length === 0) {
      console.log(chalk.gray('白名单为空'))
      console.log(chalk.gray('使用 weflow-cli whitelist add <昵称> 添加'))
      return
    }
    console.log(chalk.cyan(`白名单 (${entries.length}):\n`))
    console.log(chalk.gray('序号  wxid                          昵称              添加时间'))
    console.log(chalk.gray('─'.repeat(80)))
    for (let i = 0; i < entries.length; i++) {
      const e = entries[i]
      const num = String(i + 1).padStart(2)
      const id = (e.wxid || '').padEnd(28)
      const name = (e.displayName || '').slice(0, 16).padEnd(16)
      const ts = e.addedAt ? new Date(e.addedAt).toLocaleString('zh-CN') : '-'
      console.log(`${num}  ${id} ${name} ${ts}`)
    }
  })

whitelistCmd
  .command('add <target>')
  .description('添加白名单 (支持 wxid/昵称/备注名/序号)')
  .option('--dry-run', '仅预览，不修改白名单')
  .option('--yes', '确认执行')
  .option('--json', '输出 JSON 格式')
  .action(async (target: string, opts) => {
    // 解析目标
    let wxid: string
    let displayName = target
    if (target.startsWith('wxid_') || target.includes('@chatroom') || target.includes('@openim')) {
      wxid = target
    } else {
      try {
        wxid = await resolveTalker(target)
        const sessions = await chatService.listSessions(undefined, 50)
        const match = sessions.find(s => s.username === wxid)
        if (match) displayName = match.displayName || target
      } catch (e: any) {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'TALKER_RESOLUTION_FAILED', error: e.message }))
        else console.log(chalk.red(`✗ ${e.message}`))
        if (opts.json) process.exit(1)
        return
      }
    }

    // 检查是否已在名单中
    if (whitelistService.isAllowed(wxid)) {
      if (opts.json) console.log(JSON.stringify({ success: true, action: 'whitelist.add', changed: false, target: { wxid, displayName } }))
      else console.log(chalk.yellow(`"${displayName}" (${wxid}) 已在白名单中`))
      return
    }

    // 黑名单中的目标禁止加入白名单
    if (whitelistService.isBlocked(wxid)) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'TARGET_BLOCKED', error: '目标位于黑名单中', target: { wxid, displayName } }))
        process.exit(1)
      }
      console.log(chalk.red(`\n❌ "${displayName}" (${wxid}) 在黑名单中, 禁止加入白名单`))
      console.log(chalk.gray(`  先解除: weflow-cli blacklist rm ${wxid}\n`))
      return
    }

    const preview = { action: 'whitelist.add', target: { wxid, displayName } }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan(`将添加 "${displayName}" (${wxid}) 到白名单`))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认添加白名单', ...preview }))
      process.exit(1)
    }

    // 三重确认
    if (!opts.yes) {
      console.log(chalk.cyan(`\n⚠️  即将添加以下联系人到白名单:`))
      console.log(chalk.white(`  昵称: ${displayName}`))
      console.log(chalk.gray(`  wxid: ${wxid}`))
      console.log(chalk.yellow(`  添加后，该联系人可向你收发消息\n`))

      const { q1 } = await inquirer.prompt([{
        type: 'confirm',
        name: 'q1',
        message: `确认添加 "${displayName}" 到白名单？`,
        default: false,
      }])
      if (!q1) { console.log(chalk.gray('已取消')); return }

      const { q2 } = await inquirer.prompt([{
        type: 'input',
        name: 'q2',
        message: `请输入 "确认" 以继续:`,
      }])
      if (q2 !== '确认') { console.log(chalk.gray('已取消')); return }
    }

    whitelistService.addDirect(wxid, displayName)
    if (opts.json) console.log(JSON.stringify({ success: true, changed: true, ...preview }))
    else console.log(chalk.green(`\n✓ 已添加 "${displayName}" (${wxid}) 到白名单`))
  })

whitelistCmd
  .command('rm <wxid>')
  .description('移除白名单中的 wxid')
  .option('--dry-run', '仅预览，不修改白名单')
  .option('--yes', '确认执行')
  .option('--json', '输出 JSON 格式')
  .action(async (wxid: string, opts) => {
    const name = whitelistService.lookupName(wxid)
    const exists = whitelistService.isAllowed(wxid)
    const preview = { action: 'whitelist.remove', target: { wxid, displayName: name } }
    if (!exists) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'NOT_FOUND', error: '目标不在白名单中', ...preview }))
      else console.log(chalk.yellow(`未找到: ${wxid}`))
      if (opts.json) process.exit(1)
      return
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan(`将从白名单移除: ${name} (${wxid})`))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认移除白名单', ...preview }))
      process.exit(1)
    }
    if (!opts.yes) {
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm', name: 'confirmed', message: `确认从白名单移除 "${name}"？`, default: false,
      }])
      if (!confirmed) { console.log(chalk.gray('已取消')); return }
    }
    if (whitelistService.remove(wxid)) {
      if (opts.json) console.log(JSON.stringify({ success: true, changed: true, ...preview }))
      else console.log(chalk.green(`✓ 已移除: ${name} (${wxid})`))
    }
  })

whitelistCmd
  .command('clear')
  .description('清空白名单')
  .option('--dry-run', '仅预览，不清空白名单')
  .option('--yes', '确认清空白名单')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    if (opts.dryRun) {
      const preview = { success: true, dryRun: true, action: 'whitelist.clear', entryCount: whitelistService.getWhitelistEntries().length }
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`白名单清空预览：将移除 ${preview.entryCount} 项。`))
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认清空白名单' }))
        process.exit(1)
      }
      const { confirm } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirm',
        message: '确定要清空所有白名单吗？',
        default: false,
      }])
      if (!confirm) return
    }
    whitelistService.clear()
    if (opts.json) console.log(JSON.stringify({ success: true, action: 'whitelist.clear' }))
    else console.log(chalk.green('✓ 白名单已清空'))
  })

whitelistCmd
  .command('list')
  .description('以机器可读格式显示白名单')
  .option('--json', '输出 JSON 格式，包含本地敏感标识')
  .action((opts) => {
    const entries = whitelistService.getWhitelistEntries()
    if (opts.json) console.log(JSON.stringify({ success: true, entries }, null, 2))
    else console.log(entries.map(entry => `${entry.wxid}\t${entry.displayName || ''}`).join('\n'))
  })

// ==================== blacklist ====================
const blacklistCmd = program
  .command('blacklist')
  .description('黑名单管理 (绝对禁止收发, 优先级高于白名单; 不传参数显示列表)')

blacklistCmd
  .action(() => {
    const entries = whitelistService.getBlacklistEntries()
    if (entries.length === 0) {
      console.log(chalk.gray('黑名单为空'))
      console.log(chalk.gray('使用 weflow-cli blacklist add <昵称/wxid> 添加'))
      return
    }
    console.log(chalk.red(`黑名单 (${entries.length}):\n`))
    console.log(chalk.gray('序号  wxid                          昵称              添加时间'))
    console.log(chalk.gray('─'.repeat(80)))
    for (let i = 0; i < entries.length; i++) {
      const e = entries[i]
      const num = String(i + 1).padStart(2)
      const id = (e.wxid || '').padEnd(28)
      const name = (e.displayName || '').slice(0, 16).padEnd(16)
      const ts = e.addedAt ? new Date(e.addedAt).toLocaleString('zh-CN') : '-'
      console.log(`${num}  ${id} ${name} ${ts}`)
    }
  })

blacklistCmd
  .command('add <target>')
  .description('添加到黑名单 (自动从白名单移除)')
  .option('-r, --reason <text>', '拉黑原因 (可选)')
  .option('--dry-run', '仅预览，不修改黑名单')
  .option('--yes', '确认执行')
  .option('--json', '输出 JSON 格式')
  .action(async (target: string, opts) => {
    let wxid: string
    let displayName = target
    if (target.startsWith('wxid_') || target.includes('@chatroom') || target.includes('@openim')) {
      wxid = target
    } else {
      try {
        wxid = await resolveTalker(target)
        const sessions = await chatService.listSessions(undefined, 50)
        const match = sessions.find(s => s.username === wxid)
        if (match) displayName = match.displayName || target
      } catch (e: any) {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'TALKER_RESOLUTION_FAILED', error: e.message }))
        else console.log(chalk.red(`✗ ${e.message}`))
        if (opts.json) process.exit(1)
        return
      }
    }

    if (whitelistService.isBlocked(wxid)) {
      if (opts.json) console.log(JSON.stringify({ success: true, action: 'blacklist.add', changed: false, target: { wxid, displayName } }))
      else console.log(chalk.yellow(`"${displayName}" (${wxid}) 已在黑名单中`))
      return
    }

    const preview = { action: 'blacklist.add', target: { wxid, displayName }, reason: opts.reason || null }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan(`将添加 "${displayName}" (${wxid}) 到黑名单`))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认添加黑名单', ...preview }))
      process.exit(1)
    }

    if (!opts.yes) {
      console.log(chalk.cyan(`\n⚠️  即将拉黑以下联系人:`))
      console.log(chalk.white(`  昵称: ${displayName}`))
      console.log(chalk.gray(`  wxid: ${wxid}`))
      if (opts.reason) console.log(chalk.gray(`  原因: ${opts.reason}`))
      console.log(chalk.yellow(`  拉黑后, 该联系人绝对禁止收发消息 (即使误加白名单也拦截)\n`))

      const { q1 } = await inquirer.prompt([{
        type: 'confirm',
        name: 'q1',
        message: `确认拉黑 "${displayName}"？`,
        default: false,
      }])
      if (!q1) { console.log(chalk.gray('已取消')); return }
    }

    whitelistService.blockDirect(wxid, displayName, opts.reason)
    if (opts.json) console.log(JSON.stringify({ success: true, changed: true, ...preview }))
    else console.log(chalk.green(`\n✓ 已拉黑 "${displayName}" (${wxid})`))
  })

blacklistCmd
  .command('rm <wxid>')
  .description('从黑名单移除')
  .option('--dry-run', '仅预览，不修改黑名单')
  .option('--yes', '确认执行')
  .option('--json', '输出 JSON 格式')
  .action(async (wxid: string, opts) => {
    const name = whitelistService.lookupName(wxid)
    const exists = whitelistService.isBlocked(wxid)
    const preview = { action: 'blacklist.remove', target: { wxid, displayName: name } }
    if (!exists) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'NOT_FOUND', error: '目标不在黑名单中', ...preview }))
      else console.log(chalk.yellow(`未在黑名单中找到: ${wxid}`))
      if (opts.json) process.exit(1)
      return
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan(`将从黑名单移除: ${name} (${wxid})`))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认移除黑名单', ...preview }))
      process.exit(1)
    }
    if (!opts.yes) {
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm', name: 'confirmed', message: `确认从黑名单移除 "${name}"？`, default: false,
      }])
      if (!confirmed) { console.log(chalk.gray('已取消')); return }
    }
    if (whitelistService.unblock(wxid)) {
      if (opts.json) console.log(JSON.stringify({ success: true, changed: true, ...preview }))
      else console.log(chalk.green(`✓ 已从黑名单移除: ${name} (${wxid})`))
    }
  })

blacklistCmd
  .command('clear')
  .description('清空黑名单')
  .option('--dry-run', '仅预览，不清空黑名单')
  .option('--yes', '确认清空黑名单')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    if (opts.dryRun) {
      const preview = { success: true, dryRun: true, action: 'blacklist.clear', entryCount: whitelistService.getBlacklistEntries().length }
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`黑名单清空预览：将移除 ${preview.entryCount} 项。`))
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认清空黑名单' }))
        process.exit(1)
      }
      const { confirm } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirm',
        message: '确定要清空所有黑名单吗？',
        default: false,
      }])
      if (!confirm) return
    }
    whitelistService.clearBlacklist()
    if (opts.json) console.log(JSON.stringify({ success: true, action: 'blacklist.clear' }))
    else console.log(chalk.green('✓ 黑名单已清空'))
  })

blacklistCmd
  .command('list')
  .description('以机器可读格式显示黑名单')
  .option('--json', '输出 JSON 格式，包含本地敏感标识')
  .action((opts) => {
    const entries = whitelistService.getBlacklistEntries()
    if (opts.json) console.log(JSON.stringify({ success: true, entries }, null, 2))
    else console.log(entries.map(entry => `${entry.wxid}\t${entry.displayName || ''}`).join('\n'))
  })

// ==================== audit (发送审计日志) ====================
const auditCmd = program
  .command('audit')
  .description('发送审计日志查询 (记录所有 send 尝试)')

auditCmd
  .command('list')
  .description('查看最近发送记录 (默认最近 20 条)')
  .option('-n, --limit <number>', '最大条数', '20')
  .option('--all', '显示全部 (含失败)')
  .option('--failed', '只看失败')
  .option('--target <wxid>', '按目标 wxid 过滤')
  .option('--json', '输出 JSON 格式，不返回审计文件路径')
  .action((opts) => {
    const filter = (e: any) => {
      if (opts.target && e.targetWxid !== opts.target) return false
      if (opts.failed && e.success) return false
      if (!opts.all && !opts.failed && !e.success) {
        // 默认不显示被拦截的 (黑名单/速率/长度), 仅显示实际发送失败
        if (e.error && (e.error.includes('blacklist') || e.error.includes('rate') || e.error.includes('too long'))) return false
      }
      return true
    }
    const limit = parseCliInteger(opts.limit, 'limit', 1, 10000, opts.json)
    const entries = whitelistService.readAudit(limit, filter)
    const logPath = join(homedir(), '.weflow-cli', 'audit-send.log')
    if (opts.json) {
      console.log(JSON.stringify({ success: true, entries }, null, 2))
      return
    }
    if (entries.length === 0) {
      console.log(chalk.gray('无审计记录'))
      console.log(chalk.gray(`日志文件: ${logPath}`))
      return
    }

    console.log(chalk.cyan(`发送审计 (最近 ${entries.length} 条):\n`))
    for (const e of entries) {
      const time = new Date(e.timestamp).toLocaleString('zh-CN')
      const status = e.success ? chalk.green('✓') : chalk.red('✗')
      const name = e.targetName || e.targetWxid
      const err = e.error ? chalk.gray(` [${e.error}]`) : ''
      console.log(`${status} ${chalk.gray(`[${time}]`)} ${chalk.blue(name)} (${e.targetWxid}) <${e.kind}> ${chalk.gray(e.preview || '')}${err}`)
    }
    console.log(chalk.gray(`\n日志文件: ${logPath}`))
  })

auditCmd
  .command('stats')
  .description('审计统计 (成功/失败次数, 热门目标)')
  .option('--json', '输出 JSON 格式')
  .action((opts) => {
    const entries = whitelistService.readAudit()
    if (entries.length === 0) {
      if (opts.json) { console.log(JSON.stringify({ success: true, total: 0, succeeded: 0, failed: 0, topTargets: [] })); return }
      console.log(chalk.gray('无审计记录'))
      return
    }
    const success = entries.filter(e => e.success).length
    const failed = entries.length - success
    const byTarget = new Map<string, number>()
    for (const e of entries) {
      const key = e.targetName || e.targetWxid
      byTarget.set(key, (byTarget.get(key) || 0) + 1)
    }
    const top = [...byTarget.entries()].sort((a, b) => b[1] - a[1]).slice(0, 10)

    if (opts.json) {
      console.log(JSON.stringify({
        success: true,
        total: entries.length,
        succeeded: success,
        failed,
        topTargets: top.map(([target, count]) => ({ target, count })),
        sizeBytes: whitelistService.auditSize(),
      }, null, 2))
      return
    }

    console.log(chalk.cyan('发送审计统计:\n'))
    console.log(`  总记录: ${entries.length}`)
    console.log(`  ${chalk.green('成功')}: ${success}  ${chalk.red('失败')}: ${failed}`)
    const sizeKB = (whitelistService.auditSize() / 1024).toFixed(1)
    console.log(chalk.gray(`  日志大小: ${sizeKB} KB\n`))
    console.log(chalk.cyan('热门目标 (Top 10):'))
    for (const [name, n] of top) {
      console.log(`  ${String(n).padStart(4)}  ${name}`)
    }
  })

auditCmd
  .command('clear')
  .description('清空审计日志')
  .option('--dry-run', '仅预览，不清空审计日志')
  .option('--yes', '确认清空审计日志')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    if (opts.dryRun) {
      const preview = { success: true, dryRun: true, action: 'audit.clear', entryCount: whitelistService.readAudit().length }
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`审计日志清空预览：将移除 ${preview.entryCount} 条记录。`))
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认清空审计日志' }))
        process.exit(1)
      }
      const { confirm } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirm',
        message: '确定要清空所有审计记录吗？',
        default: false,
      }])
      if (!confirm) return
    }
    try {
      const { mkdirSync, writeFileSync } = await import('fs')
      const stateDir = join(homedir(), '.weflow-cli')
      mkdirSync(stateDir, { recursive: true })
      writeFileSync(join(stateDir, 'audit-send.log'), '', 'utf8')
      if (opts.json) console.log(JSON.stringify({ success: true, action: 'audit.clear' }))
      else console.log(chalk.green('✓ 审计日志已清空'))
    } catch (e: any) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'AUDIT_CLEAR_FAILED', error: '无法清空审计日志' }))
        process.exit(1)
      } else {
        console.log(chalk.red(`✗ 清空失败: ${e.message}`))
      }
    }
  })

// ==================== sns (朋友圈本地缓存) ====================
const snsCmd = program
  .command('sns')
  .description('朋友圈本地缓存查询 (4.x WCDB / NT 连接均支持)')

snsCmd
  .command('timeline')
  .description('查看朋友圈时间线 (本地已缓存的动态)')
  .option('-u, --user <wxid>', '过滤指定 wxid (可多次使用)', (v: string, acc: string[]) => { acc.push(v); return acc }, [] as string[])
  .option('-k, --keyword <kw>', '内容关键词')
  .option('-n, --limit <number>', '最大数量', '20')
  .option('-o, --offset <number>', '偏移量', '0')
  .option('--start <timestamp>', '开始时间戳 (秒)')
  .option('--end <timestamp>', '结束时间戳 (秒)')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    if (!configService.isConfigured()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' })); process.exit(1) }
      console.log(chalk.red('\n❌ 还没配置\n  运行: weflow-cli init\n'))
      process.exit(1)
    }

    // 先建立连接 (chatService 内部首次调用会自动 connect)
    const conn = await chatService.connect()
    if (!conn.success) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'DATABASE_CONNECTION_FAILED', error: '数据库连接失败' })); process.exit(1) }
      console.log(chalk.red(`\n❌ 数据库连接失败: ${conn.error}\n`))
      process.exit(1)
    }
    if (!chatService.isSnsSupported()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'SNS_UNAVAILABLE', error: '当前数据通道不支持朋友圈查询' })); process.exit(1) }
      console.log(chalk.red('\n❌ 当前数据通道不支持朋友圈查询'))
      console.log(chalk.gray('  支持: 4.x + WCDB API, 或 NT 连接 + sns.db 密钥'))
      console.log(chalk.gray('  NT 用户请先运行: weflow-cli sns capture-key'))
      console.log(chalk.gray('  提示: 在微信客户端打开朋友圈让数据落盘后再查询\n'))
      process.exit(1)
    }

    const usernames = Array.isArray(opts.user) ? opts.user : (opts.user ? [opts.user] : undefined)
    const limit = parseCliInteger(opts.limit, 'limit', 1, 1000, opts.json)
    const offset = parseCliInteger(opts.offset, 'offset', 0, 1000000, opts.json)
    const startTime = opts.start ? parseCliInteger(opts.start, 'start', 0, Number.MAX_SAFE_INTEGER, opts.json) : undefined
    const endTime = opts.end ? parseCliInteger(opts.end, 'end', 0, Number.MAX_SAFE_INTEGER, opts.json) : undefined
    const result = await chatService.getSnsTimeline({
      limit,
      offset,
      usernames,
      keyword: opts.keyword,
      startTime,
      endTime,
    })

    if (!result.success || !result.timeline || result.timeline.length === 0) {
      if (opts.json) { console.log(JSON.stringify({ success: result.success, timeline: [], error: result.error || null })); return }
      console.log(chalk.gray(`未找到朋友圈动态${result.error ? `: ${result.error}` : ''}`))
      console.log(chalk.gray('提示: 在微信客户端打开朋友圈让数据落盘后再查询'))
      return
    }

    if (opts.json) {
      console.log(JSON.stringify({ success: true, timeline: result.timeline }, null, 2))
      return
    }
    console.log(chalk.cyan(`朋友圈时间线 (${result.timeline.length} 条):\n`))
    for (const item of result.timeline) {
      const ts = item.create_time || item.createTime || item.timestamp
      const time = ts ? new Date(Number(ts) * 1000).toLocaleString('zh-CN') : '未知时间'
      const author = item.username || item.user_name || item.wxid || item.nickname || '未知'
      const content = (item.content || item.text || '').replace(/\n/g, ' ').slice(0, 80)
      console.log(chalk.gray(`[${time}]`) + ` ${chalk.blue(author)}: ${content}`)
    }
  })

snsCmd
  .command('users')
  .description('列出本地缓存中有朋友圈动态的 wxid')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    if (!configService.isConfigured()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' })); process.exit(1) }
      console.log(chalk.red('\n❌ 还没配置\n  运行: weflow-cli init\n'))
      process.exit(1)
    }
    const conn = await chatService.connect()
    if (!conn.success) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'DATABASE_CONNECTION_FAILED', error: '数据库连接失败' })); process.exit(1) }
      console.log(chalk.red(`\n❌ 数据库连接失败: ${conn.error}\n`))
      process.exit(1)
    }
    if (!chatService.isSnsSupported()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'SNS_UNAVAILABLE', error: '当前数据通道不支持朋友圈查询' })); process.exit(1) }
      console.log(chalk.red('\n❌ 当前数据通道不支持朋友圈查询\n'))
      process.exit(1)
    }

    const result = await chatService.getSnsUsernames()
    if (!result.success || !result.usernames || result.usernames.length === 0) {
      if (opts.json) { console.log(JSON.stringify({ success: result.success, usernames: [], error: result.error || null })); return }
      console.log(chalk.gray('本地缓存中无朋友圈动态'))
      return
    }

    if (opts.json) {
      console.log(JSON.stringify({ success: true, usernames: result.usernames }, null, 2))
      return
    }
    console.log(chalk.cyan(`朋友圈用户 (${result.usernames.length}):\n`))
    for (let i = 0; i < result.usernames.length; i++) {
      console.log(`  ${String(i + 1).padStart(3)}. ${result.usernames[i]}`)
    }
  })

snsCmd
  .command('stats')
  .description('朋友圈本地缓存统计')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    if (!configService.isConfigured()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' })); process.exit(1) }
      console.log(chalk.red('\n❌ 还没配置\n  运行: weflow-cli init\n'))
      process.exit(1)
    }
    const conn = await chatService.connect()
    if (!conn.success) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'DATABASE_CONNECTION_FAILED', error: '数据库连接失败' })); process.exit(1) }
      console.log(chalk.red(`\n❌ 数据库连接失败: ${conn.error}\n`))
      process.exit(1)
    }
    if (!chatService.isSnsSupported()) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'SNS_UNAVAILABLE', error: '当前数据通道不支持朋友圈查询' })); process.exit(1) }
      console.log(chalk.red('\n❌ 当前数据通道不支持朋友圈查询\n'))
      process.exit(1)
    }

    const myWxid = configService.get('wxid') || undefined
    const result = await chatService.getSnsExportStats(myWxid)
    if (!result.success || !result.data) {
      if (opts.json) { console.log(JSON.stringify({ success: false, code: 'SNS_STATS_FAILED', error: result.error || '获取统计失败' })); return }
      console.log(chalk.gray(`获取统计失败${result.error ? `: ${result.error}` : ''}`))
      return
    }

    const d = result.data
    if (opts.json) {
      console.log(JSON.stringify({ success: true, stats: d }, null, 2))
      return
    }
    console.log(chalk.cyan('朋友圈本地缓存统计:\n'))
    console.log(`  本地缓存动态总数: ${d.totalPosts}`)
    console.log(`  涉及好友数:       ${d.totalFriends}`)
    if (d.myPosts !== null) {
      console.log(`  我的动态数:       ${d.myPosts}`)
    }
    console.log(chalk.gray('\n注: 仅统计本地缓存，需在微信客户端打开朋友圈让数据落盘'))
  })

snsCmd
  .command('capture-key')
  .description('从微信进程捕获 sns.db 解密密钥 (需打开微信朋友圈触发)')
  .option('--dry-run', '仅预览，不扫描进程或修改配置')
  .option('--yes', '确认启动人工密钥捕获流程')
  .option('--json', '输出机器可读预览；实际捕获必须在交互终端执行')
  .action(async (opts) => {
    const preview = {
      success: true,
      dryRun: true,
      action: 'sns.capture-key',
      interactiveRequired: true,
      scansProcessMemory: true,
      writesEncryptedConfiguration: true,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan('捕获预览：需要管理员终端，并由用户在微信中打开朋友圈触发'))
      return
    }
    if (opts.json) {
      console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'INTERACTIVE_REQUIRED' }))
      process.exit(1)
    }
    console.log(chalk.cyan('\n🔑 捕获 sns.db 解密密钥\n'))
    console.log(chalk.yellow('准备工作:'))
    console.log('  1. 确保微信 4.x (Weixin.exe) 已登录运行')
    console.log('  2. 确保当前终端以管理员身份运行')
    console.log('  3. 准备好点击微信中的"朋友圈"标签\n')

    if (!opts.yes) {
      const { confirm } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirm',
        message: '准备好了吗？点击确认后请立即打开微信朋友圈',
        default: false,
      }])
      if (!confirm) {
        console.log(chalk.gray('已取消'))
        return
      }
    }

    // Check sns.db path
    let snsPath = configService.get('snsDbPath')
    if (!snsPath) {
      // Auto-detect
      try {
        const ntResult = await NtCore.scan()
        if (ntResult.databases) {
          const snsDb = ntResult.databases.find((db: any) => db.name === 'sns/sns.db')
          if (snsDb) {
            snsPath = snsDb.path
            configService.set('snsDbPath', snsDb.path)
            configService.set('snsSalt', snsDb.salt)
            console.log(chalk.green('✓ 已自动发现 sns.db'))
            console.log(chalk.gray('  数据库位置与盐值: 已识别，未显示'))
          }
        }
      } catch (e) {
        // Ignore scan errors
      }
    }
    if (!snsPath) {
      console.log(chalk.red('\n❌ 未找到 sns.db 路径，请手动设置:'))
      console.log(chalk.gray('  weflow-cli config set snsDbPath <path>'))
      console.log(chalk.gray('  weflow-cli config set snsSalt <32位hex>'))
      return
    }

    console.log(chalk.cyan('\n正在 Hook 微信进程...'))
    console.log(chalk.yellow('⚠  请立即在微信中打开"朋友圈"标签！(30 秒内)\n'))

    try {
      const result = await keyService.captureSnsKey(30000, snsPath, (msg) => {
        console.log(chalk.gray(`  ${msg}`))
      })

      if (result.success && result.key) {
        configService.set('snsKey', result.key)
        console.log(chalk.green(`\n✓ 成功捕获 sns.db 密钥!`))
        console.log(chalk.white('  密钥: 已安全保存'))
        console.log(chalk.cyan('\n现在可以使用朋友圈功能:'))
        console.log(chalk.gray('  weflow-cli sns timeline'))
        console.log(chalk.gray('  weflow-cli sns users'))
        console.log(chalk.gray('  weflow-cli sns stats'))
      } else {
        console.log(chalk.red(`\n❌ 捕获失败: ${result.error || '超时'}`))
        console.log(chalk.gray('提示:'))
        console.log(chalk.gray('  1. 确保在 30 秒内打开了朋友圈'))
        console.log(chalk.gray('  2. 如果有多个 wxid 目录，请手动设置 snsDbPath'))
        console.log(chalk.gray('  3. 或手动设置密钥: weflow-cli config set snsKey <64位hex>'))
      }
    } catch (e: any) {
      console.log(chalk.red(`\n❌ 错误: ${e.message}`))
    }
  })

// ==================== fav (微信收藏) ====================

const FAV_TYPE_IDS: Record<string, number> = {
  text: 1, image: 2, video: 4, article: 5, chatrecord: 14,
}
const FAV_TYPE_LABELS: Record<string, string> = {
  text: '文字', image: '图片', video: '视频', article: '文章', chatrecord: '聊天记录',
}

const favCmd = program
  .command('fav')
  .description('微信收藏内容查询 (4.x NT 连接)')

favCmd
  .command('list')
  .description('列出收藏内容')
  .option('-t, --type <type>', '类型过滤: text|image|video|article|chatrecord')
  .option('-k, --keyword <kw>', '关键词搜索')
  .option('-n, --limit <number>', '最大数量', '20')
  .option('-o, --offset <number>', '偏移量', '0')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    const limit = parseCliInteger(opts.limit, 'limit', 1, 5000, !!opts.json)
    const offset = parseCliInteger(opts.offset, 'offset', 0, 1_000_000, !!opts.json)
    if (!configService.isConfigured()) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' }))
      else console.log(chalk.red('\n❌ 还没配置\n  运行: weflow-cli init\n'))
      process.exit(1)
    }

    // 自动发现 favorite.db
    if (!configService.get('favDbPath')) {
      const favPath = detectFavDbPath()
      if (favPath) {
        configService.set('favDbPath', favPath)
        if (!opts.json) console.log(chalk.green(`✓ 自动发现收藏数据库: ${favPath}\n`))
      }
    }

    const conn = await chatService.connect()
    if (!conn.success) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'DATABASE_CONNECTION_FAILED', error: '数据库连接失败' }))
      else console.log(chalk.red(`\n❌ 数据库连接失败: ${conn.error}\n`))
      process.exit(1)
    }
    const favReason = chatService.favUnavailableReason()
    if (favReason) {
      const detail = {
        channel: '当前数据通道不支持收藏查询 (需 4.x NT 连接)',
        path: '未找到收藏数据库 (favorite.db)，请确认微信已登录并产生过收藏',
        key: '收藏数据库已找到，但缺少密钥',
      }[favReason]
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'FAVORITES_UNAVAILABLE', reason: favReason, error: detail }))
        process.exit(1)
      }
      console.log(chalk.red(`\n❌ ${detail}`))
      if (favReason === 'key') {
        console.log(chalk.gray('  请先配置收藏密钥: weflow-cli fav set-key <64位hex密钥>'))
        console.log(chalk.gray('  或设置全库 passphrase: weflow-cli fav set-key --passphrase <64位hex>'))
      }
      if (favReason === 'path') {
        console.log(chalk.gray('  可手动指定: weflow-cli config set favDbPath <favorite.db 路径>'))
      }
      console.log('')
      process.exit(1)
    }

    let favType: number | undefined
    if (opts.type) {
      favType = FAV_TYPE_IDS[opts.type.toLowerCase()]
      if (!favType) {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_FAVORITE_TYPE', error: '收藏类型无效' }))
        else {
          console.log(chalk.red(`❌ 未知类型: ${opts.type}`))
          console.log(chalk.gray('  可选: text image video article chatrecord'))
        }
        process.exit(1)
      }
    }

    const result = await chatService.getFavorites({
      limit,
      offset,
      keyword: opts.keyword,
      favType,
    })

    if (!result.success || !result.favorites || result.favorites.length === 0) {
      if (opts.json) {
        console.log(JSON.stringify({ success: result.success, favorites: [], total: result.total || 0, error: result.error || null }))
        if (!result.success) process.exit(1)
      } else console.log(chalk.gray(`未找到收藏内容${result.error ? `: ${result.error}` : ''}`))
      return
    }

    if (opts.json) {
      console.log(JSON.stringify(result, null, 2))
      return
    }

    const total = result.total ?? result.favorites.length
    console.log(chalk.cyan(`收藏列表 (共 ${total} 条, 显示 ${result.favorites.length} 条):\n`))
    for (const item of result.favorites) {
      const time = item.update_time
        ? new Date(Number(item.update_time) * 1000).toLocaleString('zh-CN')
        : '未知时间'
      const typeLabel = FAV_TYPE_LABELS[item.type_name] || item.type_name
      const title = (item.title || '(无标题)').replace(/\n/g, ' ').slice(0, 60)
      console.log(chalk.gray(`[${time}]`) + ` ${chalk.blue(`[${typeLabel}]`)} ${title}`)
      if (item.source_name) console.log(chalk.gray(`    来源: ${item.source_name}`))
      if (item.link) console.log(chalk.gray(`    ${item.link.slice(0, 90)}`))
    }
  })

favCmd
  .command('export <format>')
  .description('导出收藏内容 (markdown|json)')
  .option('-t, --type <type>', '类型过滤: text|image|video|article|chatrecord')
  .option('-k, --keyword <kw>', '关键词搜索')
  .option('-n, --limit <number>', '最大数量', '1000')
  .option('-o, --output <file>', '输出文件路径')
  .option('--json-result', '输出机器可读的导出结果，不改变文件格式')
  .action(async (format: string, opts) => {
    if (format !== 'markdown' && format !== 'json') {
      if (opts.jsonResult) console.log(JSON.stringify({ success: false, code: 'INVALID_FORMAT', error: '格式仅支持 markdown 或 json' }))
      else console.log(chalk.red('❌ 格式仅支持: markdown | json'))
      process.exit(1)
    }
    const limit = parseCliInteger(opts.limit, 'limit', 0, 1_000_000, !!opts.jsonResult)
    if (!configService.isConfigured()) {
      if (opts.jsonResult) console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED', error: '未完成初始化' }))
      else console.log(chalk.red('\n❌ 还没配置\n  运行: weflow-cli init\n'))
      process.exit(1)
    }

    if (!configService.get('favDbPath')) {
      const favPath = detectFavDbPath()
      if (favPath) configService.set('favDbPath', favPath)
    }

    const conn = await chatService.connect()
    if (!conn.success) {
      if (opts.jsonResult) console.log(JSON.stringify({ success: false, code: 'DATABASE_CONNECTION_FAILED', error: '数据库连接失败' }))
      else console.log(chalk.red(`\n❌ 数据库连接失败: ${conn.error}\n`))
      process.exit(1)
    }
    if (!chatService.isFavSupported()) {
      if (opts.jsonResult) console.log(JSON.stringify({ success: false, code: 'FAVORITES_UNAVAILABLE', error: '当前数据通道不支持收藏查询' }))
      else {
        console.log(chalk.red('\n❌ 当前数据通道不支持收藏查询 (需 4.x NT 连接)'))
        console.log(chalk.gray('  请先配置收藏密钥: weflow-cli fav set-key <64位hex密钥>\n'))
      }
      process.exit(1)
    }

    let favType: number | undefined
    if (opts.type) {
      favType = FAV_TYPE_IDS[opts.type.toLowerCase()]
      if (!favType) {
        if (opts.jsonResult) console.log(JSON.stringify({ success: false, code: 'INVALID_FAVORITE_TYPE', error: '收藏类型无效' }))
        else console.log(chalk.red(`❌ 未知类型: ${opts.type}`))
        process.exit(1)
      }
    }

    // 全量导出: limit 为 0 时取全部
    const result = await chatService.getFavorites({
      limit,
      offset: 0,
      keyword: opts.keyword,
      favType,
    })
    if (!result.success || !result.favorites) {
      if (opts.jsonResult) console.log(JSON.stringify({ success: false, code: 'EXPORT_FAILED', error: result.error || '无内容' }))
      else console.log(chalk.red(`导出失败: ${result.error || '无内容'}`))
      process.exit(1)
    }

    const items = result.favorites
    let content: string
    if (format === 'json') {
      content = JSON.stringify(items, null, 2)
    } else {
      const lines: string[] = ['# 微信收藏导出', '', `> 共 ${items.length} 条 · 导出时间 ${new Date().toLocaleString('zh-CN')}`, '']
      for (const item of items) {
        const time = item.update_time
          ? new Date(Number(item.update_time) * 1000).toLocaleString('zh-CN')
          : '未知时间'
        const typeLabel = FAV_TYPE_LABELS[item.type_name] || item.type_name
        lines.push(`## ${item.title || '(无标题)'}`)
        lines.push('')
        lines.push(`- 类型: ${typeLabel}`)
        lines.push(`- 时间: ${time}`)
        if (item.source_name) lines.push(`- 来源: ${item.source_name}`)
        if (item.link) lines.push(`- 链接: <${item.link}>`)
        if (item.desc) lines.push(`- 摘要: ${item.desc}`)
        lines.push('')
      }
      content = lines.join('\n')
    }

    const outPath = opts.output || `favorites_${Date.now()}.${format === 'json' ? 'json' : 'md'}`
    writeFileSync(outPath, content, 'utf8')
    if (opts.jsonResult) console.log(JSON.stringify({ success: true, format, path: outPath, count: items.length }, null, 2))
    else console.log(chalk.green(`✓ 已导出 ${items.length} 条收藏到 ${outPath}`))
  })

favCmd
  .command('set-key [key]')
  .description('设置收藏数据库密钥 (默认 raw key; --passphrase 表示全库共用 passphrase)')
  .option('--passphrase', '输入的是全库共用 passphrase (自动派生 favorite.db 密钥)')
  .option('--from-env <name>', '从环境变量读取密钥，避免密钥出现在命令参数中', 'WEFLOW_FAV_KEY')
  .option('--dry-run', '仅验证输入并预览，不修改配置')
  .option('--yes', '确认执行')
  .option('--json', '输出 JSON 格式，不返回密钥或数据库路径')
  .action(async (key: string | undefined, opts) => {
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(opts.fromEnv)) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_ENVIRONMENT_NAME', error: '环境变量名称无效' }))
      else console.log(chalk.red('环境变量名称无效'))
      process.exit(1)
    }
    const resolvedKey = key || process.env[opts.fromEnv]
    if (!resolvedKey) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'KEY_MISSING', error: `环境变量 ${opts.fromEnv} 未设置且未提供密钥` }))
      else console.log(chalk.red(`✗ 未提供密钥；请设置环境变量 ${opts.fromEnv} 或传入 key`))
      process.exit(1)
    }
    if (!/^[0-9a-fA-F]{64}$/.test(resolvedKey)) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_KEY_FORMAT', error: '密钥必须是 64 位十六进制字符串' }))
      else console.log(chalk.red('❌ 密钥格式错误: 需要 64 位十六进制字符串'))
      process.exit(1)
    }
    const preview = {
      action: 'favorites.set-key',
      mode: opts.passphrase ? 'passphrase' : 'raw-key',
      source: key ? 'argument' : 'environment',
      environment: key ? null : opts.fromEnv,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan(`将保存收藏数据库${opts.passphrase ? '全库 passphrase' : '密钥'}`))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认保存收藏数据库密钥', ...preview }))
      process.exit(1)
    }
    if (opts.passphrase) {
      configService.set('favPassphrase', resolvedKey)
      configService.set('favKey', '') // 清空旧 raw key, 下次查询时重新派生
      if (!opts.json) console.log(chalk.green('✓ 已保存全库 passphrase'))
    } else {
      configService.set('favKey', resolvedKey)
      if (!opts.json) console.log(chalk.green('✓ 已保存 favorite.db raw key'))
    }
    let databaseDetected = !!configService.get('favDbPath')
    if (!configService.get('favDbPath')) {
      const favPath = detectFavDbPath()
      if (favPath) {
        configService.set('favDbPath', favPath)
        databaseDetected = true
        if (!opts.json) console.log(chalk.green(`✓ 自动发现收藏数据库: ${favPath}`))
      } else if (!opts.json) {
        console.log(chalk.yellow('⚠ 未自动发现 favorite.db, 请手动设置: weflow-cli config set favDbPath <path>'))
      }
    }
    if (opts.json) {
      console.log(JSON.stringify({ success: true, changed: true, databaseDetected, ...preview }))
      return
    }
    console.log(chalk.cyan('\n现在可以使用收藏功能:'))
    console.log(chalk.gray('  weflow-cli fav list'))
    console.log(chalk.gray('  weflow-cli fav export markdown'))
  })

// ==================== send ====================
program
  .command('send <target> <message>')
  .description('发送文本消息给指定联系人')
  .option('--image <path>', '发图片')
  .option('--file <path>', '发文件')
  .option('--dry-run', '仅预览不实际发送')
  .option('--yes', '确认实际发送')
  .option('--json', '输出 JSON 格式；实际发送仍需 --yes')
  .option('--rate-window <ms>', '速率窗口毫秒', '60000')
  .option('--rate-max <n>', '窗口内最大发送条数', '10')
  .action(async (target: string, message: string, opts) => {
    if (opts.image && opts.file) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'CONFLICTING_MEDIA', error: '--image 与 --file 不能同时使用' }))
      else console.log(chalk.red('--image 与 --file 不能同时使用'))
      process.exit(1)
    }

    // Resolve target
    let wxid: string
    try {
      wxid = await resolveTalker(target, !!opts.json, !!opts.json)
    } catch (e: any) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'TALKER_RESOLUTION_FAILED', error: e.message }))
      else console.log(chalk.red(`${e.message}`))
      process.exit(1)
    }

    // 解析 displayName 用于二次确认 (优先用名单里存的)
    let displayName = whitelistService.lookupName(wxid)
    if (displayName === wxid) {
      try {
        const sessions = await chatService.listSessions(undefined, 200)
        const match = sessions.find(s => s.username === wxid)
        if (match?.displayName) displayName = match.displayName
      } catch {}
    }

    // 类型与预览
    const kind: 'text' | 'image' | 'file' = opts.image ? 'image' : opts.file ? 'file' : 'text'
    let preview = message.slice(0, 80) + (message.length > 80 ? '...' : '')
    if (kind !== 'text') {
      const mediaPath = String(opts.image || opts.file)
      try {
        const mediaStat = statSync(mediaPath)
        if (!mediaStat.isFile() || mediaStat.size === 0) throw new Error('invalid media')
        preview = `[${kind}] ${basename(mediaPath)} (${mediaStat.size} bytes)`
      } catch {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_MEDIA_FILE', kind }))
        else console.log(chalk.red('媒体路径必须指向可读取的非空文件'))
        process.exit(1)
      }
    }

    // 文本长度限制 (仅文本)
    if (kind === 'text' && message.length > MAX_TEXT_LENGTH) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'MESSAGE_TOO_LONG', error: `文本上限为 ${MAX_TEXT_LENGTH} 字符` }))
      else {
        console.log(chalk.red(`\n❌ 文本过长 (${message.length} 字符), 上限 ${MAX_TEXT_LENGTH}`))
        console.log(chalk.gray('  过长内容请拆分多条或使用 --file 发送文件\n'))
      }
      whitelistService.auditSend({
        timestamp: Date.now(), action: 'send', targetWxid: wxid, targetName: displayName,
        kind, success: false, preview, error: `text too long (${message.length})`,
      })
      process.exit(1)
    }

    // 黑名单优先拦截
    if (whitelistService.isBlocked(wxid)) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'TARGET_BLOCKED', error: '目标在黑名单中', target: { wxid, displayName } }))
      else {
        console.log(chalk.red('\n❌ 目标在黑名单中，绝对禁止发送'))
        console.log(chalk.gray(`  wxid: ${wxid}`))
        console.log(chalk.gray(`  解除: weflow-cli blacklist rm ${wxid}\n`))
      }
      whitelistService.auditSend({
        timestamp: Date.now(), action: 'send', targetWxid: wxid, targetName: displayName,
        kind, success: false, preview, error: 'blocked by blacklist',
      })
      process.exit(1)
    }

    // Whitelist check
    if (!whitelistService.isAllowed(wxid)) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'TARGET_NOT_ALLOWED', error: '目标不在白名单中', target: { wxid, displayName } }))
      else {
        console.log(chalk.red('\n❌ 目标不在白名单中，拒绝发送'))
        console.log(chalk.gray(`  先运行: weflow-cli whitelist add ${target}`))
        console.log(chalk.gray(`  查看名单: weflow-cli whitelist\n`))
      }
      process.exit(1)
    }

    // 速率限制 (dry-run 不计入, 不检查也行, 但保持一致检查)
    const windowMs = Number(opts.rateWindow)
    const max = Number(opts.rateMax)
    if (!Number.isInteger(windowMs) || windowMs < 1000 || windowMs > 3_600_000 ||
        !Number.isInteger(max) || max < 1 || max > 100) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_RATE_LIMIT', error: 'rate-window 必须为 1000-3600000，rate-max 必须为 1-100' }))
      else console.log(chalk.red('速率参数无效：rate-window 必须为 1000-3600000，rate-max 必须为 1-100'))
      process.exit(1)
    }
    const rate = whitelistService.checkRateLimit(windowMs, max)
    if (!rate.allowed) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'RATE_LIMITED', error: '触发发送速率限制', rateLimit: rate }))
      else {
        console.log(chalk.red(`\n❌ 触发速率限制: 最近 ${rate.windowMs / 1000}s 内已发送 ${rate.count} 条 (上限 ${rate.max})`))
        console.log(chalk.gray('  请稍后再试, 或调整 --rate-window / --rate-max\n'))
      }
      whitelistService.auditSend({
        timestamp: Date.now(), action: 'send', targetWxid: wxid, targetName: displayName,
        kind, success: false, preview, error: `rate limited (${rate.count}/${rate.max})`,
      })
      process.exit(1)
    }

    // 二次确认 — 同时显示 wxid + displayName + 消息预览
    const actionPreview = {
      action: 'send',
      target: { wxid, displayName },
      kind,
      preview,
      rateLimit: { count: rate.count, max: rate.max, windowMs: rate.windowMs },
    }
    if (!opts.json) {
      console.log(chalk.cyan('\n⚠️  即将发送:'))
      console.log(chalk.white(`  目标: ${displayName}`))
      console.log(chalk.gray(`  wxid: ${wxid}`))
      console.log(chalk.gray(`  类型: ${kind}`))
      console.log(chalk.gray(`  预览: ${preview}`))
      console.log(chalk.gray(`  速率: ${rate.count}/${rate.max} (窗口 ${rate.windowMs / 1000}s)\n`))
    }

    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...actionPreview }))
      else console.log(chalk.yellow('⚠️  --dry-run 模式, 不实际发送'))
      return
    }

    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认发送', ...actionPreview }))
      process.exit(1)
    }

    if (!opts.yes) {
      const { confirm } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirm',
        message: `确认发送给 ${chalk.cyan(displayName)} (${wxid})？`,
        default: false,
      }])
      if (!confirm) {
        console.log(chalk.gray('已取消'))
        return
      }
    }

    // Login check
    const token = configService.get('wechatOcToken')
    if (!token) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'MESSAGE_CHANNEL_NOT_LOGGED_IN', error: '消息通道未登录' }))
      else {
        console.log(chalk.red('\n❌ 未登录消息通道'))
        console.log(chalk.gray('  先运行: weflow-cli login-wechat\n'))
      }
      process.exit(1)
    }

    const service = new WechatMessageService({ token })

    let success = false
    let errorMsg: string | undefined
    try {
      if (opts.image) {
        success = await service.sendImage(wxid, opts.image)
      } else if (opts.file) {
        success = await service.sendFile(wxid, opts.file)
      } else {
        success = await service.sendText(wxid, message)
      }
    } catch {
      errorMsg = '消息通道调用失败'
    }

    if (!success && !errorMsg) {
      // 检查是否因缺少 context_token
      const hasToken = !!(configService.getContextTokens()[wxid])
      errorMsg = hasToken
        ? '发送失败 (接口返回错误, 可能 token 过期)'
        : '缺少 context_token — 需先收到对方一条消息 (或运行 weflow-cli listen 等待对方消息)'
    }

    // 审计日志
    whitelistService.auditSend({
      timestamp: Date.now(),
      action: 'send',
      targetWxid: wxid,
      targetName: displayName,
      kind,
      success,
      preview,
      error: success ? undefined : errorMsg,
    })

    if (success) {
      if (opts.json) console.log(JSON.stringify({ success: true, ...actionPreview }))
      else console.log(chalk.green('✓ 发送成功'))
    } else {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'SEND_FAILED', error: errorMsg, ...actionPreview }))
      else console.log(chalk.red(`✗ 发送失败 — ${errorMsg}`))
      if (opts.json) process.exit(1)
    }
  })

// ==================== listen ====================
program
  .command('listen')
  .description('监听微信消息 (Ctrl+C 退出)')
  .option('--target <wxid>', '只显示指定用户的消息')
  .option('--dry-run', '仅预览，不连接消息通道或输出消息')
  .option('--yes', '确认启动人工前台监听')
  .option('--json', '输出机器可读预览；实际监听必须在交互终端执行')
  .action(async (opts) => {
    const preview = {
      success: true,
      dryRun: true,
      action: 'wechat-channel.listen',
      interactiveRequired: true,
      outputsMessageContent: true,
      targetRestricted: !!opts.target,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan('监听预览：将前台持续接收并显示白名单消息，需 Ctrl+C 退出'))
      return
    }
    if (opts.json) {
      console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'INTERACTIVE_REQUIRED' }))
      process.exit(1)
    }
    if (!opts.yes) {
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: '确认在当前终端持续监听并显示白名单消息？',
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消'))
        return
      }
    }
    const token = configService.get('wechatOcToken')
    if (!token) {
      console.log(chalk.red('\n❌ 未登录消息通道'))
      console.log(chalk.gray('  先运行: weflow-cli login-wechat\n'))
      process.exit(1)
    }

    // Whitelist must be non-empty before listening
    const wl = whitelistService.getList()
    if (wl.length === 0) {
      console.log(chalk.red('\n❌ 白名单为空，拒绝监听'))
      console.log(chalk.gray('  为了防止收到非预期消息，必须先配置白名单'))
      console.log(chalk.gray('  运行: weflow-cli whitelist add <昵称>\n'))
      process.exit(1)
    }
    console.log(chalk.gray(`当前白名单: ${wl.length} 项`))
    const bl = whitelistService.getBlacklist()
    if (bl.length > 0) {
      console.log(chalk.gray(`当前黑名单: ${bl.length} 项 (绝对拦截)`))
    }

    const service = new WechatMessageService({ token })
    console.log(chalk.cyan('\n开始监听消息... (Ctrl+C 退出)\n'))

    service.onMessage((msg) => {
      // Whitelist filter (strict)
      if (!whitelistService.isAllowed(msg.fromUserId)) {
        return
      }
      // Target filter
      if (opts.target && msg.fromUserId !== opts.target) {
        return
      }

      const time = new Date(msg.timestampMs).toLocaleString('zh-CN')
      const kind = msg.messageKind !== 'text' ? ` [${msg.messageKind}]` : ''
      console.log(chalk.gray(`[${time}]`) + ` ${chalk.blue(msg.senderNickname || '未知发送者')}:${kind} ${msg.messageStr}`)
    })

    // Graceful shutdown
    process.on('SIGINT', async () => {
      console.log(chalk.gray('\n正在停止监听...'))
      await service.stop()
      process.exit(0)
    })

    await service.startPolling()
  })

// ==================== report ====================
program
  .command('report')
  .description('生成聊天月报（AI 分析任务和回复）')
  .option('--month <YYYY-MM>', '指定月份')
  .option('--talker <昵称>', '指定联系人（可多次使用）', (value: string, previous: string[]) => [...previous, value], [] as string[])
  .option('--from-whitelist', '使用白名单中的联系人')
  .option('--api-key <key>', 'DeepSeek API key')
  .option('--no-ai', '仅统计，不调用 AI')
  .option('-o, --output <dir>', '输出目录', './output')
  .option('--dry-run', '仅预览，不读取聊天、调用 AI 或写入报告')
  .option('--yes', '确认生成报告')
  .option('--json', '输出机器可读结果，不返回联系人或本地路径')
  .action(async (opts) => {
    if (opts.month && !/^\d{4}-(0[1-9]|1[0-2])$/.test(opts.month)) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_DATE', field: 'month' }))
      else console.log(chalk.red('month 必须是有效的 YYYY-MM 月份'))
      process.exit(1)
    }
    const talkers = opts.talker as string[]
    const noAi = opts.ai === false
    const preview = {
      success: true,
      dryRun: true,
      action: 'report.generate',
      monthSpecified: !!opts.month,
      talkerCount: talkers.length,
      fromWhitelist: !!opts.fromWhitelist,
      aiEnabled: !noAi,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`月报预览：${talkers.length || (opts.fromWhitelist ? '白名单' : '默认')} 个指定对象，AI ${noAi ? '关闭' : '开启'}`))
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
        process.exit(1)
      }
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: `确认生成聊天月报？${noAi ? '仅执行本地统计。' : '所选聊天内容将发送到已配置的 AI 服务。'}`,
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消'))
        return
      }
    }
    if (!configService.isConfigured()) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'NOT_INITIALIZED' }))
      else console.log(chalk.red('\n❌ 还没配置\n  运行: weflow-cli init\n'))
      process.exit(1)
    }

    const { execFile } = await import('child_process')
    const { promisify } = await import('util')
    const execFileAsync = promisify(execFile)
    const pkgRoot = resolvePackageRoot()
    const script = join(pkgRoot, 'scripts', 'chat_report.py')

    const args: string[] = [script]
    if (opts.month) args.push('--month', opts.month)
    if (opts.fromWhitelist) args.push('--from-whitelist')
    if (noAi) args.push('--no-ai')
    if (opts.output) args.push('--output', opts.output)

    try {
      if (!opts.json) console.log(chalk.cyan('正在生成月报...\n'))
      const { stdout } = await execFileAsync(getPythonCommand(), args, {
        timeout: 600_000,
        maxBuffer: 50 * 1024 * 1024,
        env: {
          ...pythonProcessEnv(opts.apiKey),
          ...(talkers.length > 0 ? { WEFLOW_REPORT_TALKERS: JSON.stringify(talkers) } : {}),
        },
      })
      if (opts.json) console.log(JSON.stringify({ success: true, action: 'report.generate' }))
      else console.log(stdout)
    } catch (e: any) {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'REPORT_FAILED', error: safeSubprocessError(e, '生成失败') }))
      else console.error(chalk.red(`\n✗ ${safeSubprocessError(e, '生成失败')}`))
      process.exit(1)
    }
  })

  // vault init
  program
    .command('vault')
    .description('Obsidian Vault 管理')
    .addCommand(
      new Command('init')
        .description('初始化 Obsidian Vault 目录结构')
        .option('-p, --path <dir>', 'Vault 路径', './output/wechat-vault')
        .option('--dry-run', '仅预览将创建或覆盖的项目，不修改文件')
        .option('--yes', '确认创建目录并写入模板文件')
        .option('--json', '输出 JSON 格式，不返回本地路径或文件名')
        .action(async (opts) => {
          const { mkdirSync, writeFileSync } = await import('fs')
          const { join } = await import('path')

          const vaultPath = opts.path
          const dirs = [
            '.obsidian',
            'Templates',
            'Sources/WeChat',
            '000_Inbox',
            '001_Daily',
            '002_Literature/WeChat',
            '002_Literature/WeRead',
            '003_Ideas',
            '004_Permanent',
            '005_Reference/Tools',
            '005_Reference/Methods',
            '006_Projects',
            '007_Wiki/Concepts',
            '008_MOC',
            '999_Archive',
            '_attachments',
            'Wiki/Concepts',
            'Wiki/Entities',
            'Wiki/Topics',
          ]
          const files = [
            {
              relativePath: join('.obsidian', 'app.json'),
              content: JSON.stringify({
                newFileLocation: 'folder',
                newFileFolderPath: 'Sources',
                attachmentFolderPath: 'Assets',
                showInlineTitle: false,
              }, null, 2),
            },
            {
              relativePath: join('Templates', 'article.md'),
              content: [
                '---',
                'title: "{{title}}"',
                'source: ""',
                'date: {{date}}',
                'topic: AI',
                'tags: []',
                'created: {{date}}',
                '---',
                '',
                '# {{title}}',
                '',
                '> 来源：  ',
                '> 时间：{{date}}  ',
                '',
                '---',
                '',
                '## AI 摘要',
                '',
                '',
                '## 相关概念',
                '',
                '',
                '---',
                '',
                '## 正文',
                '',
              ].join('\n'),
            },
            {
              relativePath: 'README.md',
              content: [
                '# WeChat Knowledge Vault',
                '',
                '> 由 weflow-cli 自动生成，兼容 Obsidian。',
                '',
                '## 目录结构',
                '',
                '| 目录 | 说明 |',
                '|------|------|',
                '| `Sources/WeChat/` | 公众号文章（按日期+主题分类） |',
                '| `Wiki/Concepts/` | 概念页（手动或 AI 生成） |',
                '| `Wiki/Entities/` | 实体页（公众号、作者等） |',
                '| `Wiki/Topics/` | 主题总览页 |',
                '| `Templates/` | 模板文件 |',
                '',
                '## 快速查询',
                '',
                '使用 Obsidian Dataview 插件：',
                '',
                '```dataview',
                'TABLE date, topic, tags',
                'FROM "Sources/WeChat"',
                'WHERE topic = "AI"',
                'SORT date DESC',
                '```',
                '',
                '```dataview',
                'TABLE length(rows) as "篇数"',
                'FROM "Sources/WeChat"',
                'GROUP BY topic',
                'SORT rows.length DESC',
                '```',
                '',
                '## 每日更新',
                '',
                '```bash',
                '# 生成今日日报',
                'python scripts/biz_daily.py --api-key <key>',
                '',
                '# 后处理（广告清洗+深度摘要）',
                'python scripts/classify_daily.py --api-key <key> --interest AI',
                '',
                '# 同步到 GitHub',
                '# (见 OPERATIONS.md)',
                '```',
                '',
                '---',
                '',
                '*由 weflow-cli vault init 生成*',
              ].join('\n'),
            },
            {
              relativePath: '.gitignore',
              content: [
                '.obsidian/workspace*.json',
                '.obsidian/hotkeys.json',
                '.trash/',
                '.DS_Store',
              ].join('\n'),
            },
          ]
          const directoryCreateCount = dirs.filter(dir => !existsSync(join(vaultPath, dir))).length
          const fileCreateCount = files.filter(file => !existsSync(join(vaultPath, file.relativePath))).length
          const overwriteCount = files.length - fileCreateCount
          const preview = {
            success: true,
            dryRun: true,
            action: 'vault.init',
            directoryCreateCount,
            fileCreateCount,
            overwriteCount,
          }

          if (opts.dryRun) {
            if (opts.json) console.log(JSON.stringify(preview))
            else {
              console.log(chalk.cyan(`Vault 初始化预览：新建 ${directoryCreateCount} 个目录、${fileCreateCount} 个文件，覆盖 ${overwriteCount} 个文件`))
            }
            return
          }

          if (!opts.yes) {
            if (opts.json) {
              console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
              process.exit(1)
            }
            const { confirmed } = await inquirer.prompt([{
              type: 'confirm',
              name: 'confirmed',
              message: `确认初始化 Vault？将新建 ${directoryCreateCount} 个目录、${fileCreateCount} 个文件，并覆盖 ${overwriteCount} 个已有文件。`,
              default: false,
            }])
            if (!confirmed) {
              console.log(chalk.gray('已取消'))
              return
            }
          }

          if (!opts.json) console.log(chalk.cyan(`\n🔧 初始化 Obsidian Vault: ${vaultPath}\n`))
          for (const dir of dirs) {
            mkdirSync(join(vaultPath, dir), { recursive: true })
            if (!opts.json) console.log(`  ✓ ${dir}/`)
          }
          for (const file of files) {
            writeFileSync(join(vaultPath, file.relativePath), file.content, 'utf8')
          }

          if (opts.json) {
            console.log(JSON.stringify({
              success: true,
              action: 'vault.init',
              directoryCreateCount,
              fileCreateCount,
              overwriteCount,
            }))
          } else {
            console.log(chalk.green(`\n✓ Vault 创建完成!`))
            console.log(`  用 Obsidian 打开: File → Open Vault → ${vaultPath}`)
          }
        })
    )

  // wiki compile
  program
    .command('wiki')
    .description('概念图谱管理')
    .addCommand(
      new Command('compile')
        .description('扫描文章 [[Wikilinks]] 聚合生成概念页')
        .option('-l, --limit <n>', '最多生成概念数', '20')
        .option('--source <dir>', '文章目录', './output/biz-daily')
        .option('-o, --output <dir>', '概念页输出目录', './output/wechat-vault/Wiki/Concepts')
        .option('--api-key <key>', 'DeepSeek API key')
        .option('--dry-run', '仅预览，不读取文章、调用 AI 或写入概念页')
        .option('--yes', '确认调用 AI 并生成概念页')
        .option('--json', '输出机器可读结果，不返回概念或本地路径')
        .action(async (opts) => {
          const pkgRoot = resolvePackageRoot()
          const script = join(pkgRoot, 'scripts', 'compile_wiki.py')

          const limit = parseCliInteger(opts.limit, 'limit', 1, 1000, opts.json)
          await runConfirmedPythonMutation({
            action: 'wiki.compile',
            script,
            args: ['--limit', String(limit), '--source', opts.source, '--output', opts.output],
            cliOptions: opts,
            preview: { limit, readsLocalArticles: true, usesAi: true, writesConceptPages: true },
            confirmationMessage: `确认调用 AI 并生成最多 ${limit} 个概念页？`,
            apiKey: opts.apiKey,
            timeout: 300_000,
          })
        })
    )

  // pipeline run
  program
    .command('pipeline')
    .description('端到端自动化流水线')
    .addCommand(
      new Command('run')
        .description('一键运行 biz_daily → classify → wiki compile')
        .option('--date <YYYY-MM-DD>', '日期')
        .option('--api-key <key>', 'DeepSeek API key')
        .option('--engine <name>', 'AI 引擎: deepseek / claude / ollama / local', 'deepseek')
        .option('--interest <topic>', '兴趣主题', 'AI')
        .option('--wiki-limit <n>', '概念编译数', '20')
        .option('--source <name>', '仅处理指定公众号，可重复使用', (value: string, previous: string[]) => [...previous, value], [] as string[])
        .option('--skip-classify', '跳过 AI 后处理')
        .option('--skip-wiki', '跳过概念编译')
        .option('--skip-vault', '跳过 Vault 副本同步')
        .option('--skip-html', '跳过 HTML 阅读器生成')
        .option('--skip-ai-report', '跳过 AI 深度阅读报告')
        .option('--no-ai', '关闭全部 AI 调用，保留抓取和本地输出')
  .option('--no-summary', '只判断不生成：biz_daily 不调 LLM 写摘要/标签/简报（判断仍走 Jev）')
        .option('--ai-report-range <n>', 'AI 报告覆盖最近 N 天', '1')
        .option('--dry-run', '仅预览步骤，不读取聊天数据、调用网络或写入文件')
        .option('--yes', '确认运行流水线')
        .option('--json', '输出机器可读结果，不返回本地路径')
        .action(async (opts) => {
          const allowedEngines = new Set(['deepseek', 'claude', 'ollama', 'local'])
          if (!allowedEngines.has(opts.engine)) {
            if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_ARGUMENT', field: 'engine' }))
            else console.log(chalk.red('engine 必须是 deepseek、claude、ollama 或 local'))
            process.exit(1)
          }
          if (opts.date) {
            try {
              parseLocalDateOrIso(opts.date)
              if (!/^\d{4}-\d{2}-\d{2}$/.test(opts.date)) throw new DateRangeError('INVALID_DATE', '日期无效')
            } catch {
              if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_DATE', field: 'date' }))
              else console.log(chalk.red('date 必须是有效的 YYYY-MM-DD 日期'))
              process.exit(1)
            }
          }
          const wikiLimit = parseCliInteger(opts.wikiLimit, 'wiki-limit', 1, 1000, opts.json)
          const aiReportRange = parseCliInteger(opts.aiReportRange, 'ai-report-range', 1, 365, opts.json)
          const sources = opts.source as string[]
          const noAi = opts.ai === false
          const preview = {
            success: true,
            dryRun: true,
            action: 'pipeline.run',
            dateSpecified: !!opts.date,
            sourceCount: sources.length,
            engine: opts.engine,
            aiEnabled: !noAi,
            cloudAiEnabled: !noAi && ['deepseek', 'claude'].includes(opts.engine),
            vaultSyncEnabled: !opts.skipVault,
            htmlEnabled: !opts.skipHtml,
            wikiEnabled: !opts.skipWiki && !noAi,
            classifyEnabled: !opts.skipClassify && !noAi,
            aiReportEnabled: !opts.skipAiReport && !noAi,
          }
          if (opts.dryRun) {
            if (opts.json) console.log(JSON.stringify(preview))
            else console.log(chalk.cyan(`流水线预览：${sources.length || '全部'} 个指定来源，AI ${noAi ? '关闭' : '开启'}，Vault 同步 ${opts.skipVault ? '关闭' : '开启'}`))
            return
          }
          if (!opts.yes) {
            if (opts.json) {
              console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
              process.exit(1)
            }
            const { confirmed } = await inquirer.prompt([{
              type: 'confirm',
              name: 'confirmed',
              message: `确认运行流水线？将抓取内容并写入本地输出${opts.skipVault ? '' : '，同时替换当天 Vault 副本'}。`,
              default: false,
            }])
            if (!confirmed) {
              console.log(chalk.gray('已取消'))
              return
            }
          }
          const { execFile } = await import('child_process')
          const { promisify } = await import('util')
          const execFileAsync = promisify(execFile)
          const pkgRoot = resolvePackageRoot()
          const script = join(pkgRoot, 'scripts', 'pipeline.py')

          const args: string[] = [
            script,
            '--engine', opts.engine,
            '--interest', opts.interest,
            '--wiki-limit', String(wikiLimit),
            '--ai-report-range', String(aiReportRange),
          ]
          if (opts.date) args.push('--date', opts.date)
          for (const source of sources) args.push('--source', source)
          if (opts.skipClassify) args.push('--skip-classify')
          if (opts.skipWiki) args.push('--skip-wiki')
          if (opts.skipVault) args.push('--skip-vault')
          if (opts.skipHtml) args.push('--skip-html')
          if (opts.skipAiReport) args.push('--skip-ai-report')
          if (noAi) args.push('--no-ai')
          if (opts.noSummary && !noAi) args.push('--no-summary')

          try {
            if (!opts.json) console.log(chalk.cyan('\n启动端到端流水线...\n'))
            const { stdout } = await execFileAsync(getPythonCommand(), args, {
              timeout: 600_000,
              maxBuffer: 50 * 1024 * 1024,
              env: pythonProcessEnv(opts.apiKey),
            })
            if (opts.json) console.log(JSON.stringify({ success: true, action: 'pipeline.run' }))
            else console.log(stdout)
          } catch (e: any) {
            if (opts.json) console.log(JSON.stringify({ success: false, code: 'PIPELINE_FAILED', error: safeSubprocessError(e, '流水线失败') }))
            else console.error(chalk.red(`\n✗ ${safeSubprocessError(e, '流水线失败')}`))
            process.exit(1)
          }
        })
    )

  // vault sync — add to existing vault command
  program.commands
    .find(c => c.name() === 'vault')
    ?.addCommand(
      new Command('sync')
        .description('增量提交 Vault 并推送到远端仓库')
        .option('-r, --repo <url>', '远端 Git 仓库地址')
        .option('-b, --branch <name>', '分支名', 'main')
        .option('--dry-run', '仅预览变更数量，不提交或推送')
        .option('--yes', '确认提交并推送')
        .option('--json', '输出 JSON 格式，不返回远端地址或文件名')
        .action(async (opts) => {
          const { execFile } = await import('child_process')
          const { promisify } = await import('util')
          const execFileAsync = promisify(execFile)
          const vaultPath = join(process.cwd(), 'output', 'wechat-vault')

          const repo = opts.repo || configService.get('vaultRepo')
          if (!repo) {
            if (opts.json) {
              console.log(JSON.stringify({ success: false, code: 'VAULT_REMOTE_REQUIRED' }))
              process.exit(1)
            }
            console.error(chalk.red('\n❌ 未指定远端仓库。运行: weflow-cli config set vaultRepo <url> 或使用 --repo 参数\n'))
            process.exit(1)
          }

          try {
            const gitDir = join(vaultPath, '.git')
            const { existsSync, readdirSync } = await import('fs')
            if (!existsSync(vaultPath)) {
              if (opts.json) console.log(JSON.stringify({ success: false, code: 'VAULT_NOT_FOUND' }))
              else console.error(chalk.red('Vault 不存在，请先运行 weflow-cli vault init'))
              process.exit(1)
            }

            const initialized = existsSync(gitDir)
            const countFiles = (dir: string): number => readdirSync(dir, { withFileTypes: true }).reduce((total, entry) => {
              if (entry.name === '.git') return total
              return total + (entry.isDirectory() ? countFiles(join(dir, entry.name)) : 1)
            }, 0)
            const status = initialized
              ? (await execFileAsync('git', ['status', '--porcelain'], { cwd: vaultPath })).stdout
              : ''
            const changeCount = initialized
              ? status.split(/\r?\n/).filter(Boolean).length
              : countFiles(vaultPath)
            const preview = {
              success: true,
              dryRun: true,
              action: 'vault.sync',
              branch: opts.branch,
              initialized,
              changeCount,
            }

            if (opts.dryRun) {
              if (opts.json) console.log(JSON.stringify(preview))
              else console.log(chalk.cyan(`Vault 同步预览：${changeCount} 个文件状态将被处理`))
              return
            }
            if (changeCount === 0) {
              if (opts.json) console.log(JSON.stringify({ success: true, action: 'vault.sync', changed: false, pushed: false }))
              else console.log(chalk.yellow('没有变更，跳过同步'))
              return
            }
            if (!opts.yes) {
              if (opts.json) {
                console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', action: 'vault.sync', changeCount }))
                process.exit(1)
              }
              const { confirmed } = await inquirer.prompt([{
                type: 'confirm',
                name: 'confirmed',
                message: `确认提交并推送 Vault 的 ${changeCount} 个文件状态？`,
                default: false,
              }])
              if (!confirmed) {
                console.log(chalk.gray('已取消'))
                return
              }
            }

            if (!existsSync(gitDir)) {
              if (!opts.json) console.log(chalk.cyan('初始化 Vault Git 仓库...'))
              await execFileAsync('git', ['init'], { cwd: vaultPath })
              await execFileAsync('git', ['remote', 'add', 'origin', repo], { cwd: vaultPath })
            } else {
              try {
                await execFileAsync('git', ['remote', 'get-url', 'origin'], { cwd: vaultPath })
              } catch {
                await execFileAsync('git', ['remote', 'add', 'origin', repo], { cwd: vaultPath })
              }
            }

            const dateStr = new Date().toISOString().slice(0, 10)
            await execFileAsync('git', ['add', '-A'], { cwd: vaultPath })
            const { stdout: statOut } = await execFileAsync('git', ['diff', '--cached', '--stat'], { cwd: vaultPath })
            if (!opts.json) console.log(chalk.cyan('提交变更...'))
            await execFileAsync('git', ['commit', '-m', `vault sync: ${dateStr} | ${statOut.split('\n').length} files`], { cwd: vaultPath })

            if (!opts.json) console.log(chalk.cyan('推送到远端...'))
            await execFileAsync('git', ['push', '-u', 'origin', opts.branch], { cwd: vaultPath, timeout: 60_000 })

            if (opts.json) console.log(JSON.stringify({ success: true, action: 'vault.sync', changed: true, pushed: true, branch: opts.branch, changeCount }))
            else console.log(chalk.green(`\n✓ Vault 已同步 (${opts.branch})`))
          } catch (e: any) {
            if (opts.json) console.log(JSON.stringify({ success: false, code: 'VAULT_SYNC_FAILED', error: safeSubprocessError(e, '同步失败') }))
            else console.error(chalk.red(`\n✗ ${safeSubprocessError(e, '同步失败')}`))
            process.exit(1)
          }
        })
    )

  // vault enrich — bidirectional backlinks
  program.commands
    .find(c => c.name() === 'vault')
    ?.addCommand(
      new Command('enrich')
        .description('增强双向链接 — 为文章添加相关阅读段落')
        .option('--date <YYYY-MM-DD>', '日期（默认今天）')
        .option('--source <dir>', '文章目录', './output/biz-daily')
        .option('--dry-run', '仅预览，不读取或修改文章')
        .option('--yes', '确认修改文章')
        .option('--json', '输出机器可读结果，不返回本地路径')
        .action(async (opts) => {
          const pkgRoot = resolvePackageRoot()
          const script = join(pkgRoot, 'scripts', 'enrich_backlinks.py')
          if (!opts.date) {
            const now = new Date()
            opts.date = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`
          }
          requireCliDate(opts.date, opts.json)
          await runConfirmedPythonMutation({
            action: 'vault.enrich',
            script,
            args: ['--date', opts.date, '--source', opts.source],
            cliOptions: opts,
            preview: { dateSpecified: true, modifiesArticles: true, usesAi: false },
            confirmationMessage: '确认向当日文章写入相关阅读链接？',
          })
        })
    )

  // vault notes — create reading notes
  program.commands
    .find(c => c.name() === 'vault')
    ?.addCommand(
      new Command('notes')
        .description('创建阅读笔记 — 为文章生成 Vault 笔记页')
        .option('--date <YYYY-MM-DD>', '日期（默认今天）')
        .option('--source <dir>', '文章目录', './output/biz-daily')
        .option('--vault <path>', 'Vault 路径', './output/wechat-vault')
        .option('--dry-run', '仅预览，不读取文章或写入笔记')
        .option('--yes', '确认生成阅读笔记')
        .option('--json', '输出机器可读结果，不返回本地路径')
        .action(async (opts) => {
          const pkgRoot = resolvePackageRoot()
          const script = join(pkgRoot, 'scripts', 'create_reading_notes.py')
          if (!opts.date) {
            const now = new Date()
            opts.date = `${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,'0')}-${String(now.getDate()).padStart(2,'0')}`
          }
          requireCliDate(opts.date, opts.json)
          await runConfirmedPythonMutation({
            action: 'vault.notes',
            script,
            args: ['--date', opts.date, '--source', opts.source, '--vault', opts.vault],
            cliOptions: opts,
            preview: { dateSpecified: true, createsNotes: true, usesAi: false },
            confirmationMessage: '确认从当日文章生成 Vault 阅读笔记？',
          })
        })
    )

  // vault tag — auto tag
  program.commands
    .find(c => c.name() === 'vault')
    ?.addCommand(
      new Command('tag')
        .description('自动标签 — AI 为文章补充标签')
        .option('--date <YYYY-MM-DD>', '日期（默认今天）')
        .option('--source <dir>', '文章目录', './output/biz-daily')
        .option('--force', '覆盖已有标签')
        .option('--api-key <key>', 'DeepSeek API key')
        .option('--dry-run', '仅预览，不读取文章、调用 AI 或修改标签')
        .option('--yes', '确认调用 AI 并修改文章标签')
        .option('--json', '输出机器可读结果，不返回本地路径或文章名')
        .action(async (opts) => {
          const pkgRoot = resolvePackageRoot()
          const script = join(pkgRoot, 'scripts', 'auto_tag.py')
          if (!opts.date) { const n=new Date(); opts.date=`${n.getFullYear()}-${String(n.getMonth()+1).padStart(2,'0')}-${String(n.getDate()).padStart(2,'0')}` }
          requireCliDate(opts.date, opts.json)
          await runConfirmedPythonMutation({
            action: 'vault.tag',
            script,
            args: ['--date', opts.date, '--source', opts.source, ...(opts.force ? ['--force'] : [])],
            cliOptions: opts,
            preview: { dateSpecified: true, force: !!opts.force, modifiesArticles: true, usesAi: true },
            confirmationMessage: `确认调用 AI 为当日文章生成标签${opts.force ? '并覆盖已有标签' : ''}？`,
            apiKey: opts.apiKey,
          })
        })
    )

  // vault search — search across Vault
  program.commands
    .find(c => c.name() === 'vault')
    ?.addCommand(
      new Command('search')
        .description('Vault 搜索 — 文章+概念+笔记')
        .argument('<query>', '搜索关键词')
        .option('--type <type>', 'all / article / concept / note', 'all')
        .option('--top-k <n>', '返回数量', '10')
        .action(async (query, opts) => {
          const pkgRoot = resolvePackageRoot()
          const script = join(pkgRoot, 'scripts', 'vault_search.py')
          if (!['all', 'article', 'concept', 'note'].includes(opts.type)) {
            console.log(chalk.red('type 必须是 all、article、concept 或 note'))
            process.exit(1)
          }
          const topK = parseCliInteger(opts.topK, 'top-k', 1, 100)
          await runPythonCmd(script, [query, '--type', opts.type, '--top-k', String(topK), '--json'])
        })
    )

  // vault rag — RAG dialog over Vault
  program.commands
    .find(c => c.name() === 'vault')
    ?.addCommand(
      new Command('rag')
        .description('Vault 问答 — 基于知识库的 AI 对话')
        .argument('<question>', '问题')
        .option('--top-k <n>', '检索条数', '8')
        .option('--api-key <key>', 'DeepSeek API key')
        .option('--dry-run', '仅预览，不读取知识库或调用 AI')
        .option('--yes', '确认读取本地知识并发送筛选后的上下文到 AI')
        .option('--json', '输出机器可读结果；执行仍需 --yes')
        .action(async (question, opts) => {
          const pkgRoot = resolvePackageRoot()
          const script = join(pkgRoot, 'scripts', 'vault_rag.py')
          const topK = parseCliInteger(opts.topK, 'top-k', 1, 100, opts.json)
          const preview = {
            success: true,
            dryRun: true,
            action: 'vault.rag',
            topK,
            readsLocalKnowledge: true,
            usesAi: true,
            sendsSelectedContextToAi: true,
          }
          if (opts.dryRun) {
            if (opts.json) console.log(JSON.stringify(preview))
            else console.log(chalk.cyan('预览：将检索本地知识库，并把最多指定条数的相关上下文发送到已配置的 AI 服务。'))
            return
          }
          if (!opts.yes) {
            if (opts.json) {
              console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
              process.exit(1)
            }
            const { confirmed } = await inquirer.prompt([{
              type: 'confirm',
              name: 'confirmed',
              message: '确认读取本地知识库并将筛选后的上下文发送到 AI 服务吗？',
              default: false,
            }])
            if (!confirmed) {
              console.log(chalk.gray('已取消'))
              return
            }
          }
          const args = ['--top-k', String(topK)]
          if (opts.json) args.push('--json')
          const { execFile } = await import('child_process')
          const { promisify } = await import('util')
          try {
            const { stdout } = await promisify(execFile)(getPythonCommand(), [script, ...args], {
              timeout: 120_000,
              maxBuffer: 5 * 1024 * 1024,
              env: {
                ...pythonProcessEnv(opts.apiKey),
                WEFLOW_VAULT_QUESTION: question,
              },
            })
            console.log(stdout)
          } catch (error) {
            if (opts.json) console.log(JSON.stringify({ success: false, code: 'VAULT_RAG_FAILED', error: safeSubprocessError(error) }))
            else console.error(chalk.red(`\n✗ ${safeSubprocessError(error)}`))
            process.exit(1)
          }
        })
    )

  // vault sync-weread — sync WeRead to Vault
  program.commands
    .find(c => c.name() === 'vault')
    ?.addCommand(
      new Command('sync-weread')
        .description('微信读书同步到 Vault')
        .option('--type <type>', 'all / shelf / notes', 'all')
        .option('--vault <path>', 'Vault 路径', './output/wechat-vault')
        .option('--dry-run', '仅预览，不请求微信读书或写入 Vault')
        .option('--yes', '确认同步微信读书数据')
        .option('--json', '输出机器可读结果，不返回书籍或本地路径')
        .action(async (opts) => {
          if (!['all', 'shelf', 'notes'].includes(opts.type)) {
            if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_ARGUMENT', field: 'type' }))
            else console.log(chalk.red('type 必须是 all、shelf 或 notes'))
            process.exit(1)
          }
          const pkgRoot = resolvePackageRoot()
          const script = join(pkgRoot, 'scripts', 'sync_weread.py')
          await runConfirmedPythonMutation({
            action: 'vault.sync-weread',
            script,
            args: ['--type', opts.type, '--vault', opts.vault],
            cliOptions: opts,
            preview: { syncType: opts.type, readsWereadCloud: true, writesVault: true },
            confirmationMessage: '确认从微信读书服务读取数据并写入本地 Vault？',
            apiKeyVariable: 'WEREAD_API_KEY',
          })
        })
    )

  const vaultPromoteCmd = new Command('promote')
    .description('从 Vault 阅读笔记生成知识索引、想法和长期笔记')

  const runVaultPromotion = async (
    scriptName: string,
    action: string,
    opts: { vault: string, withAi?: boolean, apiKey?: string, dryRun?: boolean, yes?: boolean, json?: boolean },
  ) => {
    const pkgRoot = resolvePackageRoot()
    const args = ['--vault', opts.vault]
    const apiKey = opts.apiKey || process.env.DEEPSEEK_API_KEY
    if (opts.withAi && !opts.dryRun) {
      if (!apiKey) {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'AI_KEY_REQUIRED', action }))
        else console.error(chalk.red('启用 AI 升级需要 --api-key 或 DEEPSEEK_API_KEY。'))
        process.exit(1)
      }
    } else {
      args.push('--skip-ai')
    }
    await runConfirmedPythonMutation({
      action,
      script: join(pkgRoot, 'scripts', scriptName),
      args,
      cliOptions: opts,
      preview: { usesAi: !!opts.withAi, writesVault: true },
      confirmationMessage: `确认生成 Vault 知识提升内容？${opts.withAi ? '选定笔记内容将发送到已配置的 AI 服务。' : '仅执行本地确定性处理。'}`,
      apiKey: opts.withAi ? apiKey : undefined,
    })
  }

  vaultPromoteCmd
    .command('ideas')
    .description('生成主题导航和可选的研究想法')
    .option('--vault <path>', 'Vault 路径', './output/wechat-vault')
    .option('--with-ai', '允许 AI 生成研究想法')
    .option('--api-key <key>', 'AI API key，仅与 --with-ai 一起使用')
    .option('--dry-run', '仅预览，不读取笔记、调用 AI 或写入 Vault')
    .option('--yes', '确认生成知识提升内容')
    .option('--json', '输出机器可读结果，不返回笔记或本地路径')
    .action(async (opts) => {
      await runVaultPromotion('promote_ideas.py', 'vault.promote.ideas', opts)
    })

  vaultPromoteCmd
    .command('all')
    .description('生成参考索引和可选的永久笔记、项目提案')
    .option('--vault <path>', 'Vault 路径', './output/wechat-vault')
    .option('--with-ai', '允许 AI 生成永久笔记和项目提案')
    .option('--api-key <key>', 'AI API key，仅与 --with-ai 一起使用')
    .option('--dry-run', '仅预览，不读取笔记、调用 AI 或写入 Vault')
    .option('--yes', '确认生成知识提升内容')
    .option('--json', '输出机器可读结果，不返回笔记或本地路径')
    .action(async (opts) => {
      await runVaultPromotion('promote_all.py', 'vault.promote.all', opts)
    })

  program.commands.find(c => c.name() === 'vault')?.addCommand(vaultPromoteCmd)

  // chat-stats
  program
    .command('chat-stats')
    .description('微信个人信息消费报告（支付 + 转账）')
    .option('--period <week|month>', '报告周期', 'week')
    .option('--month <YYYY-MM>', '指定月份（覆盖 --period）')
    .option('-o, --output <path>', '输出路径')
    .option('--dry-run', '仅预览，不读取聊天或写入报告')
    .option('--yes', '确认生成消费报告')
    .option('--json', '输出机器可读结果，不返回统计正文或本地路径')
    .action(async (opts) => {
      if (!['week', 'month'].includes(opts.period)) {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_ARGUMENT', field: 'period' }))
        else console.log(chalk.red('period 必须是 week 或 month'))
        process.exit(1)
      }
      if (opts.month && !/^\d{4}-(0[1-9]|1[0-2])$/.test(opts.month)) {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_DATE', field: 'month' }))
        else console.log(chalk.red('month 必须是有效的 YYYY-MM 月份'))
        process.exit(1)
      }
      const pkgRoot = resolvePackageRoot()
      const script = join(pkgRoot, 'scripts', 'chat_stats.py')
      const args: string[] = ['--period', opts.period]
      if (opts.month) { args.push('--month', opts.month) }
      if (opts.output) { args.push('--output', opts.output) }
      await runConfirmedPythonMutation({
        action: 'chat-stats.generate',
        script,
        args,
        cliOptions: opts,
        preview: { period: opts.month ? 'specified-month' : opts.period, readsLocalChat: true, includesPaymentStats: true },
        confirmationMessage: '确认读取本地聊天、支付与转账数据并生成消费报告？',
        timeout: 600_000,
      })
    })

  // generate-review
  program
    .command('review')
    .description('生成 AI 学习日报')
    .option('--date <YYYY-MM-DD>', '日期')
    .option('--api-key <key>', 'DeepSeek API key')
    .option('--engine <name>', 'AI 引擎: deepseek / claude / ollama', 'deepseek')
    .option('--source <dir>', '文章目录', './output/biz-daily')
    .option('--output <dir>', '输出目录', './output/reviews')
    .option('--dry-run', '仅预览，不读取文章、调用 AI 或写入日报')
    .option('--yes', '确认生成学习日报')
    .option('--json', '输出机器可读结果，不返回本地路径')
    .action(async (opts) => {
      if (!['deepseek', 'claude', 'ollama'].includes(opts.engine)) {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_ARGUMENT', field: 'engine' }))
        else console.log(chalk.red('engine 必须是 deepseek、claude 或 ollama'))
        process.exit(1)
      }
      if (opts.date) {
        try {
          parseLocalDateOrIso(opts.date)
          if (!/^\d{4}-\d{2}-\d{2}$/.test(opts.date)) throw new DateRangeError('INVALID_DATE', '日期无效')
        } catch {
          if (opts.json) console.log(JSON.stringify({ success: false, code: 'INVALID_DATE', field: 'date' }))
          else console.log(chalk.red('date 必须是有效的 YYYY-MM-DD 日期'))
          process.exit(1)
        }
      }
      const preview = {
        success: true,
        dryRun: true,
        action: 'review.generate',
        dateSpecified: !!opts.date,
        engine: opts.engine,
        readsLocalArticles: true,
        usesAi: true,
      }
      if (opts.dryRun) {
        if (opts.json) console.log(JSON.stringify(preview))
        else console.log(chalk.cyan(`学习日报预览：使用 ${opts.engine} 分析本地文章并写入报告`))
        return
      }
      if (!opts.yes) {
        if (opts.json) {
          console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
          process.exit(1)
        }
        const { confirmed } = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: '确认生成 AI 学习日报？文章摘要将发送到已配置的 AI 服务。',
          default: false,
        }])
        if (!confirmed) {
          console.log(chalk.gray('已取消'))
          return
        }
      }
      const { execFile } = await import('child_process')
      const { promisify } = await import('util')
      const execFileAsync = promisify(execFile)
    const pkgRoot = resolvePackageRoot()
      const script = join(pkgRoot, 'scripts', 'generate_review.py')
      const args: string[] = [script, '--engine', opts.engine, '--source', opts.source, '--output', opts.output]
      if (opts.date) args.push('--date', opts.date)
      try {
        const { stdout } = await execFileAsync(getPythonCommand(), args, {
          timeout: 120_000, maxBuffer: 10 * 1024 * 1024,
          env: pythonProcessEnv(opts.apiKey),
        })
        if (opts.json) console.log(JSON.stringify({ success: true, action: 'review.generate' }))
        else console.log(stdout)
      } catch (e: any) {
        if (opts.json) console.log(JSON.stringify({ success: false, code: 'REVIEW_FAILED', error: safeSubprocessError(e) }))
        else console.error(chalk.red(`\n✗ ${safeSubprocessError(e)}`))
        process.exit(1)
      }
    })

  // fav-server
  program
    .command('fav-server')
    .description('启动收藏服务器 — daily-server 的兼容入口')
    .option('--date <YYYY-MM-DD>', '日期（默认今天）')
    .option('--port <number>', '端口', '8765')
    .option('--open', '自动打开浏览器')
    .option('--dry-run', '仅预览，不启动服务或打开浏览器')
    .option('--yes', '确认启动本地服务')
    .option('--json', '输出机器可读状态；启动仍需 --yes')
    .action(async (opts) => {
      const { spawn } = await import('child_process')
    const pkgRoot = resolvePackageRoot()
      const script = join(pkgRoot, 'scripts', 'fav_server.py')

      if (!opts.date) {
        const now = new Date()
        const yyyy = now.getFullYear()
        const mm = String(now.getMonth() + 1).padStart(2, '0')
        const dd = String(now.getDate()).padStart(2, '0')
        opts.date = `${yyyy}-${mm}-${dd}`
      }
      requireCliDate(opts.date, opts.json)

      const port = parseCliInteger(opts.port, 'port', 1, 65535, opts.json)
      const args = [script, '--date', opts.date, '--port', String(port)]
      const preview = {
        success: true,
        dryRun: true,
        action: 'daily-reader.start',
        compatibilityAlias: 'fav-server',
        date: opts.date,
        port,
        loopbackOnly: true,
        opensBrowser: Boolean(opts.open),
      }
      if (opts.dryRun) {
        if (opts.json) console.log(JSON.stringify(preview))
        else console.log(chalk.cyan(`收藏服务启动预览：${opts.date}，本机端口 ${port}${opts.open ? '，并打开浏览器' : ''}。`))
        return
      }
      if (opts.json && !opts.yes) {
        console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
        process.exit(1)
      }
      if (opts.json) {
        const result = await startDetachedDailyReader(script, opts.date, port)
        if (result.success && opts.open) await openLocalUrl(`http://localhost:${port}`)
        console.log(JSON.stringify({
          ...result,
          action: 'daily-reader.start',
          date: opts.date,
          port,
          openedBrowser: result.success && Boolean(opts.open),
        }))
        if (!result.success) process.exit(1)
        return
      }
      console.log(chalk.cyan(`⭐ 启动收藏服务器 (${opts.date})\n`))
      console.log(chalk.gray(`  按住 Ctrl 并点击: http://localhost:${port}`))

      if (opts.open) {
        await openLocalUrl(`http://localhost:${port}`)
      }

      const child = spawn(getPythonCommand(), args, {
        stdio: 'inherit',
        env: pythonProcessEnv(),
      })
      await new Promise<void>((resolve) => child.on('exit', (code) => {
        if (code !== 0 && code !== null) process.exit(code)
        resolve()
      }))
    })

  // ==================== weread ====================
  const wereadCmd = program
    .command('weread')
    .description('微信读书助手 — 书架、统计、笔记、搜索、书评、推荐')

  wereadCmd
    .command('shelf')
    .description('查看书架')
    .option('--json', 'JSON 输出')
    .action(async (opts) => {
      const weread = await getWereadService()
      const res = await weread.shelf()
      if (!res.ok) { console.log(chalk.red(`✗ ${res.error}`)); process.exit(1) }
      const d = res.data!
      if (opts.json) { console.log(JSON.stringify(d, null, 2)); return }

      const total = d.books.length + d.albums.length + (d.mp ? 1 : 0)
      console.log(chalk.cyan(`📚 书架 (${total} 条目):\n`))
      console.log(chalk.gray(`  电子书: ${d.books.length} 本 | 有声书: ${d.albums.length} 个 | 文章收藏: ${d.mp ? '有' : '无'}`))
      if (d.archive.length) {
        console.log(chalk.gray(`  书单: ${d.archive.map((a: any) => a.name).join(', ')}`))
      }
      console.log()
      const books = d.books.sort((a: any, b: any) => b.readUpdateTime - a.readUpdateTime)
      for (let i = 0; i < Math.min(books.length, 30); i++) {
        const b = books[i]
        const num = String(i + 1).padStart(3)
        const done = b.finishReading === 1 ? chalk.green('✓') : ' '
        console.log(`${num}. ${done} ${b.title}  ${chalk.gray(b.author)}`)
      }
      if (d.books.length > 30) console.log(chalk.gray(`  ... 共 ${d.books.length} 本`))
    })

  wereadCmd
    .command('stats')
    .description('阅读统计')
    .option('--mode <mode>', 'weekly | monthly | annually | overall', 'monthly')
    .option('--json', 'JSON 输出')
    .action(async (opts) => {
      const weread = await getWereadService()
      const res = await weread.readData(opts.mode)
      if (!res.ok) { console.log(chalk.red(`✗ ${res.error}`)); process.exit(1) }
      const d = res.data!
      if (opts.json) { console.log(JSON.stringify(d, null, 2)); return }

      const h = (d.totalReadTime || 0) / 3600
      console.log(chalk.cyan(`📊 阅读统计 (${opts.mode}):\n`))
      console.log(`  总时长: ${chalk.white(h.toFixed(1) + ' 小时')}`)
      console.log(`  有效天数: ${chalk.white(String(d.readDays))}`)
      if (d.dayAverageReadTime) {
        console.log(`  日均: ${chalk.white(Math.round(d.dayAverageReadTime / 60) + ' 分钟')}`)
      }
      if (d.readStat?.length) {
        console.log(`  ${d.readStat.map((s: any) => `${s.stat}: ${chalk.white(s.counts)}`).join(' | ')}`)
      }
      if (d.preferCategoryWord) console.log(`  偏好: ${chalk.white(d.preferCategoryWord)}`)
      if (d.preferTimeWord) console.log(`  时段: ${chalk.white(d.preferTimeWord)}`)

      // 偏好分类
      if (d.preferCategory?.length) {
        console.log(chalk.cyan('\n  偏好分类:'))
        for (const c of d.preferCategory.slice(0, 5)) {
          const hh = (c.readingTime / 3600).toFixed(1)
          console.log(`    ${c.categoryTitle}: ${chalk.white(hh + 'h')} (${c.readingCount}本)`)
        }
      }

      // Read longest
      if (d.readLongest?.length) {
        console.log(chalk.cyan('\n  读最久的书:'))
        for (const item of d.readLongest.slice(0, 5)) {
          const b = item.book || item.albumInfo || {}
          const hh = (item.readTime / 3600).toFixed(1)
          console.log(`    ${b.title || '(未知)'}: ${chalk.white(hh + 'h')}  ${chalk.gray(b.author || '')}`)
        }
      }
    })

  wereadCmd
    .command('notes')
    .description('笔记划线')
    .option('--book-id <id>', '指定书籍 ID')
    .option('-n, --limit <n>', '数量', '20')
    .option('--json', 'JSON 输出')
    .action(async (opts) => {
      const weread = await getWereadService()
      const limit = parseCliInteger(opts.limit, 'limit', 1, 100, opts.json)
      if (opts.bookId) {
        const res = await weread.bookmarks(opts.bookId, limit)
        if (!res.ok) { console.log(chalk.red(`✗ ${res.error}`)); process.exit(1) }
        const d = res.data!
        if (opts.json) { console.log(JSON.stringify(d, null, 2)); return }
        console.log(chalk.cyan(`📝 划线笔记 (${d.updated?.length || 0} 条):\n`))
        for (let i = 0; i < Math.min((d.updated || []).length, limit); i++) {
          const n = d.updated[i]
          const icon = n.type === 1 ? '💬' : '📌'
          console.log(`${chalk.gray(`  [${i + 1}]`)} ${icon} ${n.markText?.slice(0, 60) || ''}`)
          if (n.content) console.log(`${chalk.cyan('      想法:')} ${n.content.slice(0, 100)}`)
          console.log()
        }
      } else {
        const res = await weread.notebooks(50)
        if (!res.ok) { console.log(chalk.red(`✗ ${res.error}`)); process.exit(1) }
        const d = res.data!
        if (opts.json) { console.log(JSON.stringify(d, null, 2)); return }
        console.log(chalk.cyan(`📝 笔记本 (${d.books?.length || 0} 本有笔记):\n`))
        for (let i = 0; i < Math.min((d.books || []).length, 30); i++) {
          const b: any = d.books[i]
          const bookInfo = b.book || b
          const num = String(i + 1).padStart(3)
          console.log(`${num}. ${bookInfo.title || '(未知)'}  ${chalk.gray(`划线:${b.highlightCount || b.noteCount || 0} 想法:${b.reviewCount || 0} 书签:${b.bookmarkCount || 0}`)}`)
        }
      }
    })

  wereadCmd
    .command('search')
    .description('搜索书籍')
    .argument('<keyword>', '搜索关键词')
    .option('-n, --limit <n>', '数量', '10')
    .option('--json', 'JSON 输出')
    .action(async (keyword, opts) => {
      const weread = await getWereadService()
      const limit = parseCliInteger(opts.limit, 'limit', 1, 100, opts.json)
      const res = await weread.search(keyword, limit)
      if (!res.ok) { console.log(chalk.red(`✗ ${res.error}`)); process.exit(1) }
      const d = res.data!
      if (opts.json) { console.log(JSON.stringify(d, null, 2)); return }
      console.log(chalk.cyan(`🔍 搜索: "${keyword}" (${d.books?.length || 0} 条):\n`))
      for (let i = 0; i < (d.books || []).length; i++) {
        const b = d.books[i]
        const num = String(i + 1).padStart(2)
        const star = b.rating ? ` ⭐${b.rating}` : ''
        console.log(`  ${num}. ${b.title}  ${chalk.gray(b.author)}${star}`)
        if (b.intro) console.log(`     ${chalk.gray(b.intro.slice(0, 80))}`)
      }
    })

  wereadCmd
    .command('book')
    .description('书籍详情')
    .argument('<bookId>', '书籍 ID')
    .option('--json', 'JSON 输出')
    .action(async (bookId, opts) => {
      const weread = await getWereadService()
      const [infoRes, progressRes] = await Promise.all([
        weread.bookInfo(bookId),
        weread.getProgress(bookId),
      ])
      if (!infoRes.ok) { console.log(chalk.red(`✗ ${infoRes.error}`)); process.exit(1) }
      const b = infoRes.data!
      if (opts.json) { console.log(JSON.stringify(b, null, 2)); return }

      console.log(chalk.cyan(`📖 ${b.title}`))
      if (b.author) console.log(`  作者: ${chalk.white(b.author)}`)
      if (b.rating) console.log(`  评分: ${chalk.white('⭐' + b.rating + ' (' + (b as any).ratingCount + '评)')}`)
      if (b.category) console.log(`  分类: ${chalk.gray(b.category)}`)
      if (b.wordCount) console.log(`  字数: ${chalk.gray(b.wordCount)}`)
      if (b.publisher) console.log(`  出版: ${chalk.gray(b.publisher)}`)
      if (b.intro) console.log(`\n  ${b.intro.slice(0, 200)}`)
      if (progressRes.ok && progressRes.data) {
        console.log(`\n  📍 进度: ${chalk.white(progressRes.data.chapterTitle || '未开始')}`)
      }
    })

  wereadCmd
    .command('review')
    .description('书籍点评')
    .argument('<bookId>', '书籍 ID')
    .option('-n, --limit <n>', '数量', '10')
    .option('--json', 'JSON 输出')
    .action(async (bookId, opts) => {
      const weread = await getWereadService()
      const limit = parseCliInteger(opts.limit, 'limit', 1, 100, opts.json)
      const res = await weread.reviews(bookId, limit)
      if (!res.ok) { console.log(chalk.red(`✗ ${res.error}`)); process.exit(1) }
      const d = res.data!
      if (opts.json) { console.log(JSON.stringify(d, null, 2)); return }
      console.log(chalk.cyan(`💬 书评 (${d.reviews?.length || 0} 条):\n`))
      for (let i = 0; i < (d.reviews || []).length; i++) {
        const r = d.reviews[i]
        const num = String(i + 1).padStart(2)
        const star = r.rating ? ` ⭐${r.rating}` : ''
        console.log(`  ${num}. ${r.user?.name || '匿名'}${star}  ${chalk.gray(`👍${r.likeCount || 0}`)}`)
        if (r.content) console.log(`     ${r.content.slice(0, 120)}`)
        console.log()
      }
    })

  wereadCmd
    .command('discover')
    .description('推荐好书')
    .option('--book-id <id>', '基于某本书的相似推荐')
    .option('-n, --limit <n>', '数量', '10')
    .option('--json', 'JSON 输出')
    .action(async (opts) => {
      const weread = await getWereadService()
      const limit = parseCliInteger(opts.limit, 'limit', 1, 100, opts.json)
      const res = opts.bookId
        ? await weread.similar(opts.bookId, limit)
        : await weread.recommend(limit)
      if (!res.ok) { console.log(chalk.red(`✗ ${res.error}`)); process.exit(1) }
      const d = res.data!
      if (opts.json) { console.log(JSON.stringify(d, null, 2)); return }
      const label = opts.bookId ? '相似推荐' : '个性化推荐'
      console.log(chalk.cyan(`🎯 ${label}:\n`))
      for (let i = 0; i < (d.books || []).length; i++) {
        const b = d.books[i]
        const num = String(i + 1).padStart(2)
        const star = b.rating ? ` ⭐${b.rating}` : ''
        console.log(`  ${num}. ${b.title}  ${chalk.gray(b.author)}${star}`)
        if (b.intro) console.log(`     ${chalk.gray(b.intro.slice(0, 80))}`)
      }
    })

  wereadCmd
    .command('profile')
    .description('个人阅读概览')
    .option('--json', 'JSON 输出')
    .action(async (opts) => {
      const weread = await getWereadService()
      const res = await weread.profile()
      if (!res.ok) { console.log(chalk.red(`✗ ${res.error}`)); process.exit(1) }
      const p = res.data!
      if (opts.json) { console.log(JSON.stringify(p, null, 2)); return }

      const totalH = (p.totalReadTime / 3600).toFixed(1)
      console.log(chalk.cyan('📖 个人阅读概览:\n'))
      console.log(`  总时长:  ${chalk.white(totalH + ' 小时')}`)
      console.log(`  阅读天数: ${chalk.white(String(p.totalReadDays) + ' 天')}`)
      console.log(`  书架:    ${chalk.white(String(p.totalBooks) + ' 本')}`)
      console.log(`  读完:    ${chalk.white(String(p.totalFinished) + ' 本')}`)
    })

  // 辅助函数
  async function runPythonCmd(script: string, args: string[], apiKey?: string, apiKeyVariable = 'DEEPSEEK_API_KEY') {
    const { execFile } = await import('child_process')
    const { promisify } = await import('util')
    const execFileAsync = promisify(execFile)
    try {
      const { stdout } = await execFileAsync(getPythonCommand(), [script, ...args], {
        timeout: 120_000, maxBuffer: 5 * 1024 * 1024,
        env: pythonProcessEnv(apiKey, apiKeyVariable),
      })
      console.log(stdout)
    } catch (e: any) {
      console.error(chalk.red(`\n✗ ${safeSubprocessError(e)}`))
      process.exit(1)
    }
  }

  async function getWereadService() {
    const key = process.env.WEREAD_API_KEY || ''
    const mod = await import('../src/services/wereadService.js')
    return new mod.WereadService(key)
  }

  // semantic-search
  program
    .command('search')
    .description('语义搜索知识库（聊天记录 + 文章）')
    .argument('<query>', '搜索关键词')
    .option('--top-k <n>', '返回数量', '10')
    .option('--api-key <key>', 'DashScope embedding API key')
    .option('--dry-run', '仅预览，不读取索引或调用向量服务')
    .option('--yes', '确认执行可能调用云端向量服务的搜索')
    .option('--json', '输出机器可读结果；执行仍需 --yes')
    .action(async (query, opts) => {
      const { execFile } = await import('child_process')
      const { promisify } = await import('util')
      const execFileAsync = promisify(execFile)
    const pkgRoot = resolvePackageRoot()
      const script = join(pkgRoot, 'scripts', 'semantic_search.py')
      const topK = parseCliInteger(opts.topK, 'top-k', 1, 100, opts.json)
      const preview = {
        success: true,
        dryRun: true,
        action: 'semantic-search.query',
        topK,
        readsLocalIndex: true,
        mayUseCloudEmbedding: true,
      }
      if (opts.dryRun) {
        if (opts.json) console.log(JSON.stringify(preview))
        else console.log(chalk.cyan('预览：将读取本地语义索引，并可能把查询发送到已配置的向量服务。'))
        return
      }
      if (!opts.yes) {
        if (opts.json) {
          console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
          process.exit(1)
        }
        const { confirmed } = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: '确认执行搜索吗？查询可能发送到已配置的云端向量服务。',
          default: false,
        }])
        if (!confirmed) {
          console.log(chalk.gray('已取消'))
          return
        }
      }
      const args: string[] = [script, 'search', '--top-k', String(topK)]
      try {
        const { stdout } = await execFileAsync(getPythonCommand(), args, {
          timeout: 60_000, maxBuffer: 10 * 1024 * 1024,
          env: {
            ...pythonProcessEnv(opts.apiKey, 'DASHSCOPE_API_KEY'),
            WEFLOW_SEARCH_QUERY: query,
          },
        })
        console.log(stdout)
      } catch (e: any) {
        console.error(chalk.red(`\n✗ ${safeSubprocessError(e, '搜索失败')}`))
        process.exit(1)
      }
    })

  program
    .command('search-index')
    .description('构建语义搜索索引')
    .option('--full', '全量重建')
    .option('--api-key <key>', 'DashScope embedding API key')
    .option('--dry-run', '仅预览，不读取数据库、调用网络或写入索引')
    .option('--yes', '确认构建或重建索引')
    .option('--json', '输出 JSON 格式')
    .action(async (opts) => {
      const preview = {
        success: true,
        dryRun: true,
        action: 'search-index.build',
        mode: opts.full ? 'full' : 'incremental',
        readsLocalData: true,
        usesCloudEmbedding: true,
        replacesExistingIndex: !!opts.full,
      }
      if (opts.dryRun) {
        if (opts.json) console.log(JSON.stringify(preview))
        else console.log(chalk.cyan(`语义索引预览：${opts.full ? '全量重建' : '增量构建'}，将读取本地数据并调用向量服务`))
        return
      }
      if (!opts.yes) {
        if (opts.json) {
          console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
          process.exit(1)
        }
        const { confirmed } = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: `确认${opts.full ? '全量重建' : '增量构建'}语义索引？本地文本将发送到已配置的向量服务。`,
          default: false,
        }])
        if (!confirmed) {
          console.log(chalk.gray('已取消'))
          return
        }
      }
      const { execFile } = await import('child_process')
      const { promisify } = await import('util')
      const execFileAsync = promisify(execFile)
    const pkgRoot = resolvePackageRoot()
      const script = join(pkgRoot, 'scripts', 'semantic_search.py')
      const args: string[] = [script, 'build']
      if (opts.full) args.push('--full')
      try {
        const { stdout } = await execFileAsync(getPythonCommand(), args, {
          timeout: 300_000, maxBuffer: 10 * 1024 * 1024,
          env: pythonProcessEnv(opts.apiKey, 'DASHSCOPE_API_KEY'),
        })
        if (opts.json) console.log(stdout.trim())
        else console.log(stdout)
      } catch (e: any) {
        console.error(chalk.red(`\n✗ ${safeSubprocessError(e)}`))
        process.exit(1)
      }
    })

  // chat
  program
    .command('chat')
    .description('RAG 智能助手 — 基于聊天记录和公众号的对话式问答')
    .argument('[question]', '要问的问题（不传则进入交互模式）')
    .option('--top-k <n>', '检索数量', '10')
    .option('--talker <name>', '限定联系人/群聊')
    .option('--api-key <key>', 'AI API key')
    .option('--dry-run', '仅预览，不读取知识库或调用 AI')
    .option('--yes', '确认读取本地知识并发送筛选后的上下文到 AI')
    .option('--json', 'JSON 输出；单次执行仍需 --yes，交互模式不可由机器启动')
    .action(async (question, opts) => {
      const { execFile, spawn } = await import('child_process')
      const { promisify } = await import('util')
      const execFileAsync = promisify(execFile)
    const pkgRoot = resolvePackageRoot()
      const script = join(pkgRoot, 'scripts', 'rag_chat.py')
      const topK = parseCliInteger(opts.topK, 'top-k', 1, 100, opts.json)
      const preview = {
        success: true,
        dryRun: true,
        action: 'rag-chat.query',
        topK,
        interactiveRequired: !question,
        readsLocalKnowledge: true,
        usesCloudEmbedding: true,
        usesAi: true,
        sendsSelectedContextToAi: true,
        conversationRestricted: Boolean(opts.talker),
      }
      if (opts.dryRun) {
        if (opts.json) console.log(JSON.stringify(preview))
        else console.log(chalk.cyan(`预览：将读取本地知识并调用 AI${question ? '' : '；未提供问题时会进入人工交互模式'}。`))
        return
      }
      if (!question && opts.json) {
        console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'INTERACTIVE_REQUIRED' }))
        process.exit(1)
      }
      if (!opts.yes) {
        if (opts.json) {
          console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
          process.exit(1)
        }
        const { confirmed } = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: '确认读取本地知识并将筛选后的上下文发送到 AI 服务吗？',
          default: false,
        }])
        if (!confirmed) {
          console.log(chalk.gray('已取消'))
          return
        }
      }
      const args: string[] = [script]
      if (question) {
        args.push('--top-k', String(topK))
        if (opts.json) args.push('--json')
        try {
          const { stdout } = await execFileAsync(getPythonCommand(), args, {
            timeout: 120_000, maxBuffer: 10 * 1024 * 1024,
            env: {
              ...pythonProcessEnv(opts.apiKey),
              WEFLOW_RAG_QUESTION: question,
              ...(opts.talker ? { WEFLOW_RAG_TALKER: opts.talker } : {}),
            },
          })
          console.log(stdout)
        } catch (e: any) {
          console.error(chalk.red(`\n✗ ${safeSubprocessError(e, '对话失败')}`))
          process.exit(1)
        }
      } else {
        // 交互模式：使用 spawn 保持终端交互
        args.push('--interactive', '--top-k', String(topK))
        const child = spawn(getPythonCommand(), args, {
          stdio: 'inherit',
          env: {
            ...pythonProcessEnv(opts.apiKey),
            ...(opts.talker ? { WEFLOW_RAG_TALKER: opts.talker } : {}),
          },
        })
        await new Promise<void>((resolve) => child.on('exit', (code) => {
          if (code !== 0) process.exit(code || 1);
          resolve();
        }))
      }
    })

  // Helper: run a Python script
  async function runPython(script: string, args: string[], apiKey?: string): Promise<void> {
    const { execFile } = await import('child_process')
    const { promisify } = await import('util')
    const execFileAsync = promisify(execFile)
    const pkgRoot = resolvePackageRoot()
    const pyArgs = [join(pkgRoot, script), ...args]
    try {
      const { stdout } = await execFileAsync(getPythonCommand(), pyArgs, {
        timeout: 120_000, maxBuffer: 5 * 1024 * 1024,
        env: pythonProcessEnv(apiKey),
      })
      console.log(stdout)
    } catch (e: any) {
      console.error(chalk.red(`\n✗ ${safeSubprocessError(e)}`))
      process.exit(1)
    }
  }

  // annual-report
  program
    .command('annual-report')
    .description('生成年度数字生活报告')
    .argument('[year]', '年份（默认今年）')
    .option('--share', '分享模式（紧凑卡片）')
    .option('--api-key <key>', 'DeepSeek API key')
    .option('--skip-ai', '跳过 AI 总结')
    .option('--output <file>', '输出文件')
    .option('--dry-run', '仅预览，不读取数据、调用 AI 或写入报告')
    .option('--yes', '确认生成年度报告')
    .option('--json', '输出机器可读结果，不返回本地路径')
    .action(async (year, opts) => {
      const reportYear = parseCliInteger(year || String(new Date().getFullYear()), 'year', 1970, 2100, opts.json)
      const preview = {
        success: true,
        dryRun: true,
        action: 'annual-report.generate',
        year: reportYear,
        shareMode: !!opts.share,
        aiEnabled: !opts.skipAi,
        readsLocalData: true,
      }
      if (opts.dryRun) {
        if (opts.json) console.log(JSON.stringify(preview))
        else console.log(chalk.cyan(`${reportYear} 年度报告预览：AI ${opts.skipAi ? '关闭' : '开启'}`))
        return
      }
      if (!opts.yes) {
        if (opts.json) {
          console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
          process.exit(1)
        }
        const { confirmed } = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: `确认生成 ${reportYear} 年度报告？${opts.skipAi ? '仅执行本地统计。' : '统计摘要可能发送到已配置的 AI 服务。'}`,
          default: false,
        }])
        if (!confirmed) {
          console.log(chalk.gray('已取消'))
          return
        }
      }
      const a = [String(reportYear)]
      if (opts.share) a.push('--share')
      if (opts.skipAi) a.push('--skip-ai')
      if (opts.output) a.push('--output', opts.output)
      if (opts.json) {
        const { execFile } = await import('child_process')
        const { promisify } = await import('util')
        try {
          await promisify(execFile)(getPythonCommand(), [join(resolvePackageRoot(), 'scripts/annual_report.py'), ...a], {
            timeout: 120_000,
            maxBuffer: 5 * 1024 * 1024,
            env: pythonProcessEnv(opts.apiKey),
          })
          console.log(JSON.stringify({ success: true, action: 'annual-report.generate', year: reportYear }))
        } catch (error) {
          console.log(JSON.stringify({ success: false, code: 'ANNUAL_REPORT_FAILED', error: safeSubprocessError(error) }))
          process.exit(1)
        }
      } else {
        await runPython('scripts/annual_report.py', a, opts.apiKey)
      }
    })

  // decide —— 本机判断层。一个 state + 一批类型化问题，一次调用。
  // state 由调用方给，命令自己**不读任何本地数据**；但它是出境调用，所以照 awaiting/search
  // 的规矩：--dry-run 只校验、--yes 才真跑。这一版**不暴露成 MCP 工具**——那会变成
  // 远端 MCP 客户端能驱动本机往第三方发任意文本，属于要单独决策的出境面。
  program
    .command('decide')
    .description('本机判断层：一个 state + 一批类型化问题，一次调用返回带概率的答案')
    .option('--request <file>', '请求 JSON 的路径（含 state 与 questions）')
    .option('--over <glob>', '批量模式：对匹配到的每个文件问 --ask 里的每个问题（可重复）',
            (value: string, previous: string[]) => previous.concat([value]), [] as string[])
    .option('--ask <text>', '批量模式下的一个是非题，逐文件展开（可重复）',
            (value: string, previous: string[]) => previous.concat([value]), [] as string[])
    .option('--max-chars <n>', '批量模式下每个文件喂多少字符（默认 1500；它决定上限）', '1500')
    .option('--limit <n>', '批量模式最多取多少个文件（默认 50）', '50')
    .option('--model <name>', '模型别名，默认 jev-latest')
    .option('--dry-run', '只校验请求并回显形状，不调用决策模型')
    .option('--yes', '确认把 state 发送到已配置的决策模型服务')
    .option('--json', '输出机器可读结果（本命令的输出本来就是 JSON）')
    .action(async (opts) => {
      const { execFile } = await import('child_process')
      const { promisify } = await import('util')
      const { readFileSync } = await import('fs')
      const execFileAsync = promisify(execFile)
      const pkgRoot = resolvePackageRoot()
      const decideScript = join(pkgRoot, 'scripts', 'decide.py')

      // 预览里要报"这批几个问题"，所以在这里读一眼；**校验仍然只由脚本做**，
      // 免得两处校验逻辑各说各话。
      // 两种输入方式：--request 一个文件、或 --over 批量展开。脚本自己会拒掉
      // 两个都没给的情况，所以这里不做重复校验。
      const batch = Array.isArray(opts.over) && opts.over.length > 0
      let questionCount: number | null = null
      if (batch) {
        questionCount = opts.over.length * opts.ask.length || null
      } else if (opts.request) {
        try {
          const parsed = JSON.parse(readFileSync(opts.request, 'utf8'))
          if (parsed && typeof parsed === 'object' && parsed.questions
              && typeof parsed.questions === 'object') {
            questionCount = Object.keys(parsed.questions).length
          }
        } catch {
          // 读不到或不是 JSON：交给脚本去报一个准确得多的错误。
        }
      }
      const limit = parseCliInteger(opts.limit, 'limit', 1, 500, opts.json)
      const maxChars = parseCliInteger(opts.maxChars, 'max-chars', 1, 100000, opts.json)

      const args = [decideScript,
                    ...(batch
                      ? [...opts.over.flatMap((g: string) => ['--over', g]),
                         ...opts.ask.flatMap((a: string) => ['--ask', a]),
                         '--max-chars', String(maxChars), '--limit', String(limit)]
                      : ['--request', opts.request]),
                    ...(opts.model ? ['--model', opts.model] : [])]
      if (opts.dryRun) {
        args.push('--dry-run')
      } else if (!opts.yes) {
        const preview = {
          success: false, dryRun: false, action: 'decide',
          code: 'CONFIRMATION_REQUIRED',
          questionCount, readsLocalData: batch, invokesAI: true,
        }
        if (opts.json) {
          console.log(JSON.stringify(preview))
          process.exit(1)
        }
        const { confirmed } = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: `确认把请求里的 state 发送到已配置的决策模型服务吗？`
            + (questionCount === null ? '' : `（${questionCount} 个问题）`),
          default: false,
        }])
        if (!confirmed) {
          console.log(chalk.gray('已取消'))
          return
        }
      }

      try {
        const { stdout } = await execFileAsync(getPythonCommand(), args, {
          timeout: 120_000, maxBuffer: 10 * 1024 * 1024,
          env: pythonProcessEnv(),
        })
        process.stdout.write(stdout)
      } catch (error) {
        const payload = (error as { stdout?: string }).stdout
        if (payload && payload.trim()) {
          // 脚本自己已经给出了结构化的失败，原样透传比自己编一个更有用。
          process.stdout.write(payload)
        } else {
          console.log(JSON.stringify({ success: false, code: 'DECIDE_FAILED',
            action: 'decide', error: safeSubprocessError(error) }))
        }
        process.exit(1)
      }
    })

  // awaiting —— 谁在等我回话。读本地聊天并把正文发给决策模型，所以照 search 的规矩：
  // --dry-run 预览、--yes 才真跑。它**不写任何本地数据**，但仍是一次正文出境，
  // 而聊天正文比日报的文章正文敏感一档。
  program
    .command('awaiting')
    .description('谁在等我回话：逐会话判断有没有欠下的回复（读取聊天正文并调用决策模型）')
    .option('--days <n>', '只看最近多少天有动静的会话', '14')
    .option('--limit <n>', '最多判多少个会话', '40')
    .option('--min-prob <p>', 'waiting 概率低于此值不算欠账', '0.5')
    .option('--dry-run', '仅预览会判哪些会话、要发多少字符，不调用决策模型')
    .option('--yes', '确认把聊天正文发送到已配置的决策模型服务')
    .option('--json', '输出机器可读结果；执行仍需 --yes')
    .action(async (opts) => {
      const { execFile } = await import('child_process')
      const { promisify } = await import('util')
      const execFileAsync = promisify(execFile)
      const pkgRoot = resolvePackageRoot()
      const script = join(pkgRoot, 'scripts', 'reply_debt.py')
      const days = parseCliInteger(opts.days, 'days', 1, 3650, opts.json)
      const limit = parseCliInteger(opts.limit, 'limit', 1, 500, opts.json)
      const args = [script, '--days', String(days), '--limit', String(limit),
                    '--min-prob', String(opts.minProb),
                    ...(opts.json ? ['--json'] : [])]

      if (opts.dryRun) {
        // 交给脚本自己报数：它才知道要发多少字符。这一步只读本地，零出境。
        args.push('--dry-run')
      } else if (!opts.yes) {
        const preview = {
          success: false, dryRun: false, action: 'reply-debt.scan',
          code: 'CONFIRMATION_REQUIRED', days, limit,
          readsLocalChat: true, invokesAI: true, writesNothing: true,
        }
        if (opts.json) {
          console.log(JSON.stringify(preview))
          process.exit(1)
        }
        const { confirmed } = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: '确认把最近这些会话的聊天正文发送到已配置的决策模型服务吗？',
          default: false,
        }])
        if (!confirmed) {
          console.log(chalk.gray('已取消'))
          return
        }
      }

      try {
        const { stdout } = await execFileAsync(getPythonCommand(), args, {
          timeout: 600_000, maxBuffer: 50 * 1024 * 1024,
          env: pythonProcessEnv(),
        })
        process.stdout.write(stdout)
      } catch (error) {
        if (opts.json) {
          console.log(JSON.stringify({ success: false, code: 'AWAITING_FAILED', action: 'reply-debt.scan', error: safeSubprocessError(error) }))
        } else {
          console.error(chalk.red(`\n✗ ${safeSubprocessError(error)}`))
        }
        process.exit(1)
      }
    })

  // todos
  const todosCmd = program
    .command('todos')
    .description('待办提取与任务追踪')

  async function runTodoMutation(action: 'done' | 'undone' | 'remove', id: string, opts: { dryRun?: boolean; yes?: boolean; json?: boolean }): Promise<void> {
    if (opts.dryRun) {
      await runPython('scripts/extract_todos.py', ['preview', id, '--action', action])
      return
    }
    if (!opts.yes) {
      if (opts.json) {
        console.log(JSON.stringify({
          success: false,
          code: 'CONFIRMATION_REQUIRED',
          error: '先使用 --dry-run --json 预览，再由用户确认后使用 --yes --json 执行',
        }))
        process.exit(1)
      }
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: `确认执行待办操作 ${action} (${id})？`,
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消'))
        return
      }
    }
    const command = action === 'remove' ? 'rm' : action
    await runPython('scripts/extract_todos.py', [command, id, ...(opts.json ? ['--json'] : [])])
  }

  todosCmd
    .command('extract')
    .description('AI 扫描聊天记录提取待办')
    .option('--days <n>', '扫描天数', '7')
    .option('--api-key <key>', 'DeepSeek API key')
    .option('--dry-run', '仅预览，不读取聊天、调用 AI 或写入待办')
    .option('--yes', '确认提取并写入待办')
    .option('--json', '输出机器可读结果，不返回聊天或待办正文')
    .action(async (opts) => {
      const days = parseCliInteger(opts.days, 'days', 1, 365, opts.json)
      await runConfirmedPythonMutation({
        action: 'todos.extract',
        script: join(resolvePackageRoot(), 'scripts', 'extract_todos.py'),
        args: ['extract', '--days', String(days)],
        cliOptions: opts,
        preview: { days, readsLocalChat: true, usesAi: true, writesTodos: true },
        confirmationMessage: `确认读取最近 ${days} 天聊天并发送到已配置的 AI 服务以提取待办？`,
        apiKey: opts.apiKey,
      })
    })

  todosCmd
    .command('list')
    .description('列出待办')
    .option('--status <s>', 'pending / done')
    .option('--urgency <u>', '高 / 中 / 低')
    .option('--json', 'JSON 输出')
    .action(async (opts) => {
      const a = ['list']
      if (opts.status) a.push('--status', opts.status)
      if (opts.urgency) a.push('--urgency', opts.urgency)
      if (opts.json) a.push('--json')
      await runPython('scripts/extract_todos.py', a)
    })

  todosCmd
    .command('done')
    .description('标记待办为已完成')
    .argument('<id>', '待办 ID')
    .option('--dry-run', '仅预览，不修改待办')
    .option('--yes', '确认执行修改')
    .option('--json', '输出 JSON 格式')
    .action(async (id, opts) => {
      await runTodoMutation('done', id, opts)
    })

  todosCmd
    .command('undone')
    .description('取消已完成标记')
    .argument('<id>', '待办 ID')
    .option('--dry-run', '仅预览，不修改待办')
    .option('--yes', '确认执行修改')
    .option('--json', '输出 JSON 格式')
    .action(async (id, opts) => {
      await runTodoMutation('undone', id, opts)
    })

  todosCmd
    .command('rm')
    .description('删除待办')
    .argument('<id>', '待办 ID')
    .option('--dry-run', '仅预览，不删除待办')
    .option('--yes', '确认执行删除')
    .option('--json', '输出 JSON 格式')
    .action(async (id, opts) => {
      await runTodoMutation('remove', id, opts)
    })

  todosCmd
    .command('remind')
    .description('查看待办提醒')
    .option('--json', '输出 JSON 格式')
    .action(async (opts) => {
      await runPython('scripts/extract_todos.py', ['remind', ...(opts.json ? ['--json'] : [])])
    })

// ==================== daily ====================
program
  .command('daily')
  .option('--source <name>', '仅处理指定公众号，可重复或用逗号分隔', (value, previous: string[] = []) => [...previous, value], [])
  .description('一键生成公众号日报（抓取 + AI 摘要 + HTML 阅读器 + 行动建议）')
  .option('-d, --date <YYYY-MM-DD>', '日期')
  .option('--api-key <key>', 'DeepSeek API key（或设环境变量 DEEPSEEK_API_KEY）')
  .option('--skip-classify', '跳过后处理')
  .option('--dry-run', '仅预览文章，不调用 AI 或写入日报')
  .option('--yes', '确认执行日报生成；JSON 模式需要此选项')
  .option('--no-ai', '关闭本次日报的所有 AI 处理')
  .option('--no-summary', '只判断不生成：不调 LLM 写摘要/标签/简报（主题与相关度仍由 Jev 判断），无需 DeepSeek key')
  .option('--json', '输出机器可读的最终结果；运行日志写入 stderr')
  .action(async (opts) => {
    const { spawn } = await import('child_process')
    const { existsSync, statSync } = await import('fs')
    const pkgRoot = resolvePackageRoot()
    const pipeline = join(pkgRoot, 'scripts', 'pipeline.py')
    const bizDaily = join(pkgRoot, 'scripts', 'biz_daily.py')

    const now = new Date()
    const localDate = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
    const date = opts.date || localDate
    const apiKey = opts.apiKey || process.env.DEEPSEEK_API_KEY || ''
    const noAi = opts.ai === false || configService.get('dailyAiEnabled') === 'false'
    const dailyLog = (...values: any[]) => opts.json ? console.error(...values) : console.log(...values)

    const artifactStatus = (targetDate: string) => {
      const outputDir = join(pkgRoot, 'output', 'biz-daily', targetDate)
      const hasFile = (file: string) => {
        const path = join(outputDir, file)
        try { return existsSync(path) && statSync(path).size > 0 } catch { return false }
      }
      return {
        readme: hasFile('README.md'),
        articleIndex: hasFile('.articles.json'),
        reader: hasFile('index.html'),
      }
    }
    const isComplete = (targetDate: string): boolean => Object.values(artifactStatus(targetDate)).every(Boolean)

    const runPipeline = (targetDate: string): Promise<number> => new Promise((resolve) => {
      const args = [pipeline, '--date', targetDate, '--engine', noAi ? 'local' : 'deepseek', '--interest', 'AI', '--skip-wiki']
      if (noAi) args.push('--no-ai')
      // 与 --no-ai 不同：这一条保留判断（Jev），只关掉 LLM 的文字生成。
      if (opts.noSummary && !noAi) args.push('--no-summary')
      for (const source of opts.source || []) args.push('--source', source)
      if (opts.skipClassify) args.push('--skip-classify')

      dailyLog(chalk.cyan(`\n📰 正在生成 ${targetDate} 公众号日报${noAi ? '（AI 已关闭）' : ''}...\n`))
      const child = spawn(getPythonCommand(), args, {
        stdio: opts.json ? ['ignore', 'pipe', 'pipe'] : 'inherit',
        env: pythonProcessEnv(apiKey),
      })
      if (opts.json) {
        child.stdout?.pipe(process.stderr)
        child.stderr?.pipe(process.stderr)
      }
      child.on('error', () => resolve(1))
      child.on('exit', (code) => resolve(code || 0))
    })

    if (opts.dryRun) {
      const dryRunArgs = [bizDaily, '--date', date, '--engine', 'local', '--dry-run']
      for (const source of opts.source || []) dryRunArgs.push('--source', source)
      const child = spawn(getPythonCommand(), dryRunArgs, {
        stdio: opts.json ? ['ignore', 'pipe', 'pipe'] : 'inherit',
        env: pythonProcessEnv(),
      })
      if (opts.json) {
        child.stdout?.pipe(process.stderr)
        child.stderr?.pipe(process.stderr)
      }
      child.on('exit', (code) => {
        if (opts.json) console.log(JSON.stringify({ success: code === 0, dryRun: true, date, aiEnabled: false }))
        process.exit(code || 0)
      })
      return
    }

    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({
        success: false,
        code: 'CONFIRMATION_REQUIRED',
        action: 'daily.generate',
        date,
        aiEnabled: !noAi,
      }))
      process.exit(1)
    }

    if (!apiKey && !noAi) {
      if (opts.json) {
        console.log(JSON.stringify({ success: false, code: 'AI_KEY_REQUIRED', error: 'AI 已启用但未配置 API key' }))
        process.exit(1)
      }
      console.log(chalk.red('\n❌ 缺少 DeepSeek API key'))
      console.log(chalk.gray('  用法: weflow-cli daily --api-key <key>'))
      console.log(chalk.gray('  或设环境变量: set DEEPSEEK_API_KEY=<key>\n'))
      process.exit(1)
    }

    if (!opts.date) {
      const previous = new Date(now)
      previous.setDate(previous.getDate() - 1)
      const previousDate = `${previous.getFullYear()}-${String(previous.getMonth() + 1).padStart(2, '0')}-${String(previous.getDate()).padStart(2, '0')}`
      if (!opts.dryRun && !isComplete(previousDate)) {
        dailyLog(chalk.yellow(`\n⚠️ 昨日报不完整，先补生成 ${previousDate}...`))
        const previousCode = await runPipeline(previousDate)
        if (previousCode !== 0 || !isComplete(previousDate)) {
          if (opts.json) {
            console.log(JSON.stringify({ success: false, code: 'PREVIOUS_DAILY_INCOMPLETE', date, previousDate }))
            process.exit(previousCode || 1)
          }
          console.log(chalk.red(`\n✗ 昨日报补生成失败或仍不完整，已停止当天日报生成。`))
          process.exit(previousCode || 1)
        }
        dailyLog(chalk.green(`\n✓ 昨日报已补齐: output/biz-daily/${previousDate}/`))
      }
    }

    const code = await runPipeline(date)
    if (code === 0) {
      const complete = isComplete(date)
      if (opts.json) {
        console.log(JSON.stringify({
          success: complete,
          code: complete ? null : 'DAILY_OUTPUT_INCOMPLETE',
          date,
          aiEnabled: !noAi,
          output: `output/biz-daily/${date}`,
          artifacts: artifactStatus(date),
        }, null, 2))
        if (!complete) process.exit(1)
        return
      }
      console.log(chalk.green(`\n✓ 日报已生成: output/biz-daily/${date}/`))
      console.log(chalk.gray(`  HTML 阅读器: weflow-cli daily-server --date ${date}`))
    } else {
      if (opts.json) console.log(JSON.stringify({ success: false, code: 'DAILY_PIPELINE_FAILED', date, aiEnabled: !noAi }))
      process.exit(code || 1)
    }
  })

const dailyFavoritesCmd = new Command('favorites')
  .description('同步或调整日报阅读器中的本地收藏')

const defaultDailyDate = () => {
  const now = new Date()
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`
}

const runDailyFavorites = async (
  date: string,
  root: string,
  changes: string[],
  action: string,
  opts: { dryRun?: boolean; yes?: boolean; json?: boolean },
) => {
  requireCliDate(date, opts.json)
  const script = join(resolvePackageRoot(), 'scripts', 'sync_fav.py')
  const itemCount = changes.length > 0 ? Math.max(0, changes.length - 1) : 0
  await runConfirmedPythonMutation({
    action,
    script,
    args: ['--date', date, '--root', root, ...changes],
    cliOptions: opts,
    preview: { dateSpecified: true, itemCount, modifiesFavoriteState: true },
    confirmationMessage: `确认更新当日日报收藏${itemCount ? `（${itemCount} 项）` : ''}？`,
  })
}

const dailyFavoriteOptions = (command: Command, opts: Record<string, any>): Record<string, any> => {
  const dailyOpts = command.parent?.parent?.opts() || {}
  return {
    ...opts,
    date: opts.date || dailyOpts.date,
    dryRun: !!(opts.dryRun || dailyOpts.dryRun),
    json: !!(opts.json || dailyOpts.json),
  }
}

dailyFavoritesCmd
  .command('sync')
  .description('按阅读器保存的收藏状态同步本地收藏目录')
  .option('-d, --date <YYYY-MM-DD>', '日报日期')
  .option('--root <dir>', '日报输出根目录', './output/biz-daily')
  .option('--dry-run', '仅预览，不读取状态或修改收藏目录')
  .option('--yes', '确认同步收藏目录')
  .option('--json', '输出机器可读结果，不返回文章或本地路径')
  .action(async (opts, command) => {
    const merged = dailyFavoriteOptions(command, opts)
    await runDailyFavorites(merged.date || defaultDailyDate(), merged.root, [], 'daily.favorites.sync', merged)
  })

dailyFavoritesCmd
  .command('add <article>')
  .description('添加日报文章到本地收藏')
  .option('--article <path>', '额外添加一篇文章，可重复使用', (value: string, previous: string[]) => [...previous, value], [] as string[])
  .option('-d, --date <YYYY-MM-DD>', '日报日期')
  .option('--root <dir>', '日报输出根目录', './output/biz-daily')
  .option('--dry-run', '仅预览，不修改收藏')
  .option('--yes', '确认添加收藏')
  .option('--json', '输出机器可读结果，不返回文章或本地路径')
  .action(async (article, opts, command) => {
    const merged = dailyFavoriteOptions(command, opts)
    const articles = [article, ...(merged.article as string[])]
    await runDailyFavorites(merged.date || defaultDailyDate(), merged.root, ['--add', ...articles], 'daily.favorites.add', merged)
  })

dailyFavoritesCmd
  .command('remove <article>')
  .description('从本地收藏中移除日报文章')
  .option('--article <path>', '额外移除一篇文章，可重复使用', (value: string, previous: string[]) => [...previous, value], [] as string[])
  .option('-d, --date <YYYY-MM-DD>', '日报日期')
  .option('--root <dir>', '日报输出根目录', './output/biz-daily')
  .option('--dry-run', '仅预览，不修改收藏')
  .option('--yes', '确认移除收藏')
  .option('--json', '输出机器可读结果，不返回文章或本地路径')
  .action(async (article, opts, command) => {
    const merged = dailyFavoriteOptions(command, opts)
    const articles = [article, ...(merged.article as string[])]
    await runDailyFavorites(merged.date || defaultDailyDate(), merged.root, ['--remove', ...articles], 'daily.favorites.remove', merged)
  })

program.commands.find(c => c.name() === 'daily')?.addCommand(dailyFavoritesCmd)

// ==================== assistant (第二大脑持久化 Agent) ====================
const assistantCmd = program
  .command('assistant')
  .description('第二大脑助手 — 微信里的持久化 AI Agent (常驻 + 记忆 + DeepSeek)')

assistantCmd
  .command('start')
  .description('后台启动守护进程 (重启电脑前持续在线)')
  .option('--dry-run', '仅预览，不启动守护进程')
  .option('--yes', '确认启动')
  .option('--json', '输出 JSON 格式；启动仍需 --yes')
  .action(async (opts) => {
    const preview = { action: 'assistant.start', sideEffects: ['background-process', 'message-processing', 'possible-ai-usage'] }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan('将后台启动第二大脑守护进程'))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认启动助手', ...preview }))
      process.exit(1)
    }
    const { startDaemon } = await import('../src/services/assistantDaemon.js')
    const r = await startDaemon()
    if (opts.json) {
      console.log(JSON.stringify(r.started
        ? { success: true, started: true, pid: r.pid, ...preview }
        : { success: false, code: 'ASSISTANT_START_FAILED', started: false, error: '助手启动失败或已经运行', ...preview }))
      if (!r.started) process.exit(1)
      return
    }
    if (r.started) {
      console.log(chalk.green(`✓ 守护进程已启动 (pid ${r.pid})`))
      console.log(chalk.gray('  在微信 ClawBot 对话里直接说话即可'))
      console.log(chalk.gray('  日志: weflow-cli assistant log'))
      console.log(chalk.gray('  停止: weflow-cli assistant stop'))
    } else {
      console.log(chalk.yellow(`⚠ ${r.error}`))
    }
  })

assistantCmd
  .command('stop')
  .description('停止守护进程')
  .option('--dry-run', '仅预览，不停止守护进程')
  .option('--yes', '确认停止')
  .option('--json', '输出 JSON 格式；停止仍需 --yes')
  .action(async (opts) => {
    const preview = { action: 'assistant.stop', sideEffects: ['background-process'] }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify({ success: true, dryRun: true, ...preview }))
      else console.log(chalk.cyan('将停止第二大脑守护进程'))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', error: '使用 --yes 确认停止助手', ...preview }))
      process.exit(1)
    }
    const { stopDaemon } = await import('../src/services/assistantDaemon.js')
    const r = stopDaemon()
    if (opts.json) {
      console.log(JSON.stringify({ success: r.stopped, stopped: r.stopped, code: r.stopped ? null : 'ASSISTANT_STOP_FAILED', ...preview }))
      if (!r.stopped) process.exit(1)
      return
    }
    console.log(r.stopped ? chalk.green(`✓ ${r.message}`) : chalk.yellow(`⚠ ${r.message}`))
  })

assistantCmd
  .command('status')
  .description('查看运行状态')
  .option('--json', '输出 JSON 格式，不返回令牌、成员或日志内容')
  .action(async (opts) => {
    const { isDaemonAlive, tailLog, rotateLogIfNeeded } = await import('../src/services/assistantDaemon.js')
    const { alive, pid } = isDaemonAlive()
    const token = configService.get('wechatOcToken')
    const aiKey = configService.get('deepseekApiKey')
    const { privacyGate } = await import('../src/services/assistantPrivacy.js')
    const wl = String(configService.get('assistantWhitelist') || '').trim()
    const groups = String(configService.get('assistantGroupWhitelist') || '').trim()
    if (opts.json) {
      console.log(JSON.stringify({
        success: true,
        daemonRunning: alive,
        messageChannelLoggedIn: !!token,
        aiConfigured: privacyGate.isLocalInference() || !!aiKey,
        localInference: privacyGate.isLocalInference(),
        privacyMode: privacyGate.mode(),
        whitelistCount: wl ? wl.split(/[,;\s]+/).filter(Boolean).length : 0,
        groupWhitelistCount: groups ? groups.split(/[,;\s]+/).filter(Boolean).length : 0,
        groupMentionRequired: configService.get('assistantGroupRequireMention') !== 'false',
      }, null, 2))
      return
    }
    console.log(`守护进程: ${alive ? chalk.green(`运行中 (pid ${pid})`) : chalk.gray('未运行')}`)
    console.log(`消息通道: ${token ? chalk.green('已登录') : chalk.red('未登录 (先 login-wechat)')}`)
    console.log(`LLM 大脑: ${aiKey ? chalk.green('DeepSeek 已配置') : chalk.gray('未配置 (config set deepseekApiKey)')}`)
    console.log(`隐私模式: ${chalk.cyan(privacyGate.mode())}${privacyGate.isLocalInference() ? chalk.green(' (本地推理, 数据不出境)') : chalk.gray(' (工具结果脱敏后出境)')}`)
    console.log(`白名单: ${wl ? chalk.green(`${wl.split(/[,;\s]+/).filter(Boolean).length} 人`) : chalk.red('未设置 (默认拒绝所有人; config set assistantWhitelist "<@im.wechat ID>")')}`)
    console.log(`群聊实验: ${groups ? chalk.yellow(`${groups.split(/[,;\s]+/).filter(Boolean).length} 个已配置，等待上游群事件`) : chalk.gray('未配置（默认拒绝所有群）')}`)
    console.log(`群聊 @ 门槛: ${configService.get('assistantGroupRequireMention') === 'false' ? chalk.red('已关闭') : chalk.green('已开启')}`)
    console.log(`用量护栏: ${chalk.cyan('100 条/天')} (微信内发「记忆」可查今日用量)`)
    rotateLogIfNeeded()
    console.log(chalk.cyan('\n最近日志:'))
    console.log(chalk.gray(tailLog(10)))
  })

assistantCmd
  .command('log')
  .description('查看守护进程日志 (最近 N 行)')
  .option('-n, --lines <number>', '行数', '30')
  .option('--json', '仅输出日志可用状态和行数，不返回日志内容')
  .action(async (opts) => {
    const { tailLog } = await import('../src/services/assistantDaemon.js')
    const lines = parseCliInteger(opts.lines, 'lines', 1, 1000, opts.json)
    const content = tailLog(lines)
    if (opts.json) {
      const available = !content.startsWith('(')
      console.log(JSON.stringify({
        success: true,
        available,
        requestedLines: lines,
        returnedLineCount: available ? content.split(/\r?\n/).length : 0,
      }))
      return
    }
    console.log(content)
  })

assistantCmd
  .command('trace')
  .description('看助手最近几轮是怎么走的（判断层 → 工具 → 答复），含模型返回的推理内容')
  .option('-n, --last <number>', '看最近几轮', '3')
  .option('--json', '输出结构化轨迹')
  .action(async (opts) => {
    const { readTurns, describeTurn, traceFile } = await import('../src/services/assistantTrace.js')
    const count = parseCliInteger(opts.last, 'last', 1, 50, opts.json)
    const turns = readTurns(count)
    if (opts.json) {
      console.log(JSON.stringify({ success: true, file: traceFile(), count: turns.length, turns }))
      return
    }
    if (!turns.length) {
      console.log('还没有轨迹。助手收到消息之后才会有；文件：' + traceFile())
      return
    }
    console.log(traceFile())
    for (const turn of turns) {
      for (const line of describeTurn(turn)) console.log(line)
      console.log('')
    }
  })

assistantCmd
  .command('run')
  .description('前台运行 (调试用; 常驻请用 start)')
  .option('--dry-run', '仅预览，不连接消息通道或调用 AI')
  .option('--yes', '确认启动人工前台调试')
  .option('--json', '输出机器可读预览；实际调试必须在交互终端执行')
  .action(async (opts) => {
    const preview = {
      success: true,
      dryRun: true,
      action: 'assistant.run',
      interactiveRequired: true,
      handlesMessages: true,
      mayUseAi: true,
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan('前台助手预览：将持续处理消息并可能调用 AI，需 Ctrl+C 退出'))
      return
    }
    if (opts.json) {
      console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'INTERACTIVE_REQUIRED' }))
      process.exit(1)
    }
    if (!opts.yes) {
      const { confirmed } = await inquirer.prompt([{
        type: 'confirm',
        name: 'confirmed',
        message: '确认在当前终端运行助手调试流程？',
        default: false,
      }])
      if (!confirmed) {
        console.log(chalk.gray('已取消'))
        return
      }
    }
    const { AssistantService } = await import('../src/services/assistantService.js')
    const assistant = new AssistantService()
    console.log(chalk.cyan('第二大脑前台运行中... (Ctrl+C 退出)'))
    await assistant.start((line) => console.log(chalk.gray(line)))
  })

program
  .command('daily-stats')
  .description('统计公众号推送频率与日报处理频率')
  .option('--days <number>', '统计最近多少天', '30')
  .option('--limit <number>', '最多显示多少个公众号', '30')
  .option('--json', '输出 JSON 格式')
  .action(async (opts) => {
    const { execFile } = await import('child_process')
    const { promisify } = await import('util')
    const execFileAsync = promisify(execFile)
    const script = join(resolvePackageRoot(), 'scripts', 'daily_stats.py')
    const days = parseCliInteger(opts.days, 'days', 1, 3650, opts.json)
    const limit = parseCliInteger(opts.limit, 'limit', 1, 1000, opts.json)

    try {
      const args = [script, '--days', String(days), '--limit', String(limit)]
      if (opts.json) args.push('--json')
      const { stdout } = await execFileAsync(getPythonCommand(), args, {
        timeout: 300_000,
        maxBuffer: 10 * 1024 * 1024,
        env: pythonProcessEnv(),
      })
      console.log(stdout)
    } catch (e: any) {
      if (opts.json) {
        console.log(JSON.stringify({
          success: false,
          code: 'DAILY_STATS_FAILED',
          error: '公众号统计不可用，请运行 check --json 检查配置和依赖',
        }))
        process.exit(1)
      }
      console.error(chalk.red(`\n${safeSubprocessError(e, '统计失败')}`))
      process.exit(1)
    }
  })

// ==================== daily-server ====================
program
  .command('daily-server')
  .description('启动本地日报阅读器（浏览器浏览 + 收藏 + 笔记）')
  .option('-d, --date <YYYY-MM-DD>', '日期')
  .option('-p, --port <n>', '端口', '8765')
  .option('--open', '自动打开浏览器')
  .option('--status', '仅检查阅读器状态，不启动服务')
  .option('--dry-run', '仅预览，不启动服务或打开浏览器')
  .option('--yes', '确认启动本地阅读器')
  .option('--json', '输出机器可读状态；启动仍需 --yes')
  .action(async (opts) => {
    const { spawn } = await import('child_process')
    const pkgRoot = resolvePackageRoot()
    const favServer = join(pkgRoot, 'scripts', 'fav_server.py')
    const port = parseCliInteger(opts.port, 'port', 1, 65535, opts.json)
    if (opts.date) requireCliDate(opts.date, opts.json)

    if (opts.status) {
      try {
        const response = await fetch(`http://127.0.0.1:${port}/api/status`, { signal: AbortSignal.timeout(2000) })
        const status = await response.json() as { ok?: boolean; service?: string; date?: string }
        const data = { success: response.ok && status.ok === true, running: response.ok, port, date: status.date || null }
        if (opts.json) console.log(JSON.stringify(data, null, 2))
        else console.log(data.running ? chalk.green(`阅读器正在运行 (${data.date || '日期未知'})`) : chalk.gray('阅读器未运行'))
      } catch {
        const data = { success: true, running: false, port, date: null }
        if (opts.json) console.log(JSON.stringify(data, null, 2))
        else console.log(chalk.gray('阅读器未运行'))
      }
      return
    }

    const date = opts.date || defaultDailyDate()
    const preview = {
      success: true,
      dryRun: true,
      action: 'daily-reader.start',
      date,
      port,
      loopbackOnly: true,
      opensBrowser: Boolean(opts.open),
    }
    if (opts.dryRun) {
      if (opts.json) console.log(JSON.stringify(preview))
      else console.log(chalk.cyan(`阅读器启动预览：${date}，本机端口 ${port}${opts.open ? '，并打开浏览器' : ''}。`))
      return
    }
    if (opts.json && !opts.yes) {
      console.log(JSON.stringify({ ...preview, success: false, dryRun: false, code: 'CONFIRMATION_REQUIRED' }))
      process.exit(1)
    }
    if (opts.json) {
      const result = await startDetachedDailyReader(favServer, date, port)
      if (result.success && opts.open) await openLocalUrl(`http://localhost:${port}`)
      console.log(JSON.stringify({
        ...result,
        action: 'daily-reader.start',
        date,
        port,
        openedBrowser: result.success && Boolean(opts.open),
      }))
      if (!result.success) process.exit(1)
      return
    }
    console.log(chalk.cyan(`⭐ 启动日报阅读器\n`))
    console.log(chalk.gray(`  日期: ${date}`))
    console.log(chalk.gray(`  地址: http://localhost:${port}`))
    console.log()

    if (opts.open) {
      await openLocalUrl(`http://localhost:${port}`)
    }

    const child = spawn(getPythonCommand(), [favServer, '--date', date, '--port', String(port)], {
      stdio: 'inherit',
      env: pythonProcessEnv(),
    })
    process.on('SIGINT', () => child.kill())
  })

// ==================== Interactive Menu (no arguments) ====================
program
  .command('mcp-config')
  .description('输出 MCP Server 配置，粘贴到 .mcp.json 即可让 AI 操作本工具')
  .option('-o, --output <file>', '写入文件', '')
  .option('--dry-run', '仅预览文件写入，不修改文件')
  .option('--yes', '确认写入配置文件')
  .option('--json-result', '写入模式输出 JSON 结果，不返回本地路径')
  .action(async (opts) => {
    const { writeFileSync } = await import('fs')
    const config = {
      mcpServers: {
        weflow: {
          command: 'npx',
          args: ['tsx', 'mcp-server/index.ts'],
          cwd: '${workspaceFolder}',
        },
      },
    }
    const json = JSON.stringify(config, null, 2)
    if (opts.output) {
      const preview = { success: true, dryRun: true, action: 'mcp-config.write', overwrite: existsSync(opts.output) }
      if (opts.dryRun) {
        if (opts.jsonResult) console.log(JSON.stringify(preview))
        else console.log(chalk.cyan(`MCP 配置写入预览：${preview.overwrite ? '将覆盖现有文件' : '将创建新文件'}`))
        return
      }
      if (!opts.yes) {
        if (opts.jsonResult) {
          console.log(JSON.stringify({ success: false, code: 'CONFIRMATION_REQUIRED', action: 'mcp-config.write', overwrite: preview.overwrite }))
          process.exit(1)
        }
        const { confirmed } = await inquirer.prompt([{
          type: 'confirm',
          name: 'confirmed',
          message: `${preview.overwrite ? '覆盖' : '创建'} MCP 配置文件？`,
          default: false,
        }])
        if (!confirmed) {
          console.log(chalk.gray('已取消'))
          return
        }
      }
      writeFileSync(opts.output, json, 'utf-8')
      if (opts.jsonResult) {
        console.log(JSON.stringify({ success: true, action: 'mcp-config.write', changed: true, overwrite: preview.overwrite }))
        return
      }
      console.log(chalk.green(`✓ 已写入 ${opts.output}`))
      console.log(chalk.gray('AI 助手现在可以使用以下工具：'))
    } else {
      console.log(json)
    }
    console.log(chalk.cyan('\n可用的 MCP 工具：'))
    const STATIC_TOOLS: Array<[string, string]> = [
      ['wechat.search_articles', '搜索知识库文章'],
      ['wechat.get_daily', '获取公众号日报(完整版)'],
      ['wechat.get_review', '获取 AI 学习日报'],
      ['wechat.get_stats', '统计概览(知识库+微信数据)'],
      ['wechat.get_concepts', '概念图谱索引'],
      ['wechat.get_concept', '读取概念 Wiki 页'],
      ['wechat.format_article', 'Markdown 转公众号排版'],
      ['wechat.list_themes', '排版主题列表'],
      ['wechat.fetch_article', '抓取公众号文章'],
      ['wechat.search_public', '搜索全网公众号文章'],
      ['wechat.export_messages', '按稳定数据契约读取会话消息'],
    ]
    const { MCP_READ_ONLY_TOOL_DEFS } = await import('../src/services/assistantTools.js')
    const all = [
      ...STATIC_TOOLS,
      ...MCP_READ_ONLY_TOOL_DEFS.filter(t => t.function.name !== 'get_stats')
        .map(t => [`wechat.${t.function.name}`, t.function.description.split(/[。(]/)[0]] as [string, string]),
    ]
    for (const [n, d] of all) {
      console.log(chalk.white('  ' + n.padEnd(28)) + chalk.gray(d))
    }
    console.log()
    if (opts.output) {
      console.log(chalk.gray(`配置已写入 ${opts.output}，重启 AI 编辑器即可生效`))
    } else {
      console.log(chalk.gray('复制以上 JSON 到你的 .mcp.json 文件即可'))
    }
  })

// ==================== check ====================
program
  .command('check')
  .description('检查运行环境（Python、pip 依赖、数据库配置）')
  .option('--json', '输出 JSON 格式，不包含数据库路径或密钥')
  .action(async (opts) => {
    if (opts.json) {
      const { execFileSync } = await import('child_process')
      const requiredDependencies = ['sqlcipher3', 'html2text', 'zstandard', 'cryptography']
      const optionalDependencies = ['scrapling']
      let pythonVersion = ''
      try {
        pythonVersion = execFileSync(getPythonCommand(), ['--version'], { encoding: 'utf8', timeout: 5000 }).trim()
      } catch {}
      const dependencyStatus = (name: string): boolean => {
        if (!pythonVersion) return false
        try {
          execFileSync(getPythonCommand(), ['-c', `import ${name}`], { stdio: 'ignore', timeout: 5000 })
          return true
        } catch {
          return false
        }
      }
      let daemonAlive = false
      try {
        const { isDaemonAlive } = await import('../src/services/assistantDaemon.js')
        daemonAlive = isDaemonAlive().alive
      } catch {}
      const engine = String(configService.get('aiEngine') || 'deepseek')
      const data = {
        success: true,
        schema: 'weflow-check/v1',
        runtime: {
          node: { available: true, version: process.versions.node },
          python: { available: !!pythonVersion, version: pythonVersion },
        },
        dependencies: {
          required: Object.fromEntries(requiredDependencies.map(name => [name, dependencyStatus(name)])),
          optional: Object.fromEntries(optionalDependencies.map(name => [name, dependencyStatus(name)])),
        },
        configuration: {
          initialized: configService.isConfigured(),
          messageDatabase: !!configService.get('ntDbPath') && existsSync(configService.get('ntDbPath')),
          momentsDatabase: !!configService.get('snsDbPath') && existsSync(configService.get('snsDbPath')),
          favoritesDatabase: !!configService.get('favDbPath') && existsSync(configService.get('favDbPath')),
          // `favoritesDatabase` only says the file is there. Reading it also
          // needs a key, and a caller that took the former for "usable" would
          // attempt favorites and always fail.
          favoritesReady: !!configService.get('favDbPath')
            && existsSync(configService.get('favDbPath'))
            && (!!configService.get('favKey') || !!configService.get('favPassphrase')),
        },
        assistant: {
          engine,
          localInference: ['ollama', 'lmstudio'].includes(engine),
          aiConfigured: ['ollama', 'lmstudio'].includes(engine) || !!configService.get('deepseekApiKey'),
          messageChannelLoggedIn: !!configService.get('wechatOcToken'),
          daemonRunning: daemonAlive,
        },
      }
      console.log(JSON.stringify(data, null, 2))
      return
    }
    console.log(chalk.cyan('🔍 WeFlow CLI 环境检查\n'))

    // 1. Node.js
    console.log(chalk.white('Node.js:'), chalk.green(`v${process.versions.node}`))

    // 2. Python
    const { execFileSync } = await import('child_process')
    const pythonCommand = getPythonCommand()
    let pythonOk = false
    try {
      const pyVer = execFileSync(pythonCommand, ['--version'], { encoding: 'utf-8', timeout: 5000 }).trim()
      console.log(chalk.white('Python: '), chalk.green(pyVer))
      pythonOk = true
    } catch {
      console.log(chalk.white('Python: '), chalk.red('✗ 未找到'))
    }

    // 3. Pip dependencies
    if (pythonOk) {
      const deps = ['sqlcipher3', 'html2text', 'zstandard', 'cryptography']
      console.log(chalk.white('\nPython 依赖:'))
      let allOk = true
      for (const dep of deps) {
        try {
          execFileSync(pythonCommand, ['-c', `import ${dep}`], { stdio: 'ignore', timeout: 5000 })
          console.log(`  ${dep.padEnd(20)} ${chalk.green('✓')}`)
        } catch {
          console.log(`  ${dep.padEnd(20)} ${chalk.red('✗ 缺失')}`)
          allOk = false
        }
      }
      if (!allOk) {
        console.log(chalk.yellow('\n⚠️  运行以下命令安装缺失依赖：'))
        if (process.platform === 'linux') {
          console.log(chalk.gray('  pip3 install --user sqlcipher3 html2text zstandard cryptography'))
          console.log(chalk.gray('  （sqlcipher3 编译需先: sudo apt install libsqlcipher-dev）'))
        } else {
          console.log(chalk.gray('  pip install sqlcipher3 html2text zstandard cryptography'))
        }
      }
      // 可选依赖
      const optDeps = ['scrapling']
      console.log(chalk.white('\n可选依赖（提升抓取成功率）:'))
      for (const dep of optDeps) {
        try {
          execFileSync(pythonCommand, ['-c', `import ${dep}`], { stdio: 'ignore', timeout: 5000 })
          console.log(`  ${dep.padEnd(20)} ${chalk.green('✓')}`)
        } catch {
          console.log(`  ${dep.padEnd(20)} ${chalk.gray('○ (pip install scrapling)')}`)
        }
      }
    }

    // 4. Config
    console.log(chalk.white('\n配置状态:'))
    const configured = configService.isConfigured()
    const hasKey = !!configService.get('ntKey') || !!configService.get('decryptKey')
    if (configured && hasKey) {
      console.log(chalk.green('  ✓ 已初始化 — 可以运行 weflow-cli'))
    } else {
      console.log(chalk.yellow('  ○ 未初始化 — 运行 weflow-cli init'))
    }

    // 5. Databases & extensions
    const dbRows: Array<[string, string, boolean]> = [
      ['消息数据库', configService.get('ntDbPath'), true],
      ['朋友圈数据库', configService.get('snsDbPath'), true],
      ['收藏数据库', configService.get('favDbPath'), true],
    ]
    for (const [label, path, keyRequired] of dbRows) {
      if (path && existsSync(path)) {
        const stat = (await import('fs')).statSync(path)
        const mb = (stat.size / 1024 / 1024).toFixed(0)
        console.log(chalk.white(`  ${label}: ${path} (${mb}MB)`))
      } else {
        const hint = keyRequired ? ' (可选, init 或 sns/fav 命令配置)' : ''
        console.log(chalk.gray(`  ${label}: 未配置${hint}`))
      }
    }

    // 6. Assistant (第二大脑)
    console.log(chalk.white('\n第二大脑 Agent:'))
    const aiKey = configService.get('deepseekApiKey')
    const aiBase = configService.get('aiBaseUrl')
    const engine = String(configService.get('aiEngine') || 'deepseek')
    if (['ollama', 'lmstudio'].includes(engine)) {
      console.log(chalk.green(`  AI 引擎: ${engine} (本地推理, 数据不出网)`))
    } else if (aiKey) {
      console.log(chalk.green(`  AI 引擎: ${aiBase ? `自定义端点 (${configService.get('aiModel') || 'deepseek-chat'})` : 'DeepSeek'}`))
    } else {
      console.log(chalk.gray('  AI 引擎: 未配置 (config set deepseekApiKey <key>)'))
    }
    const ocToken = configService.get('wechatOcToken')
    console.log(ocToken
      ? chalk.green('  消息通道: 已登录 (login-wechat)')
      : chalk.gray('  消息通道: 未登录 (weflow-cli login-wechat)'))
    try {
      const { isDaemonAlive } = await import('../src/services/assistantDaemon.js')
      const { alive, pid } = isDaemonAlive()
      console.log(alive
        ? chalk.green(`  守护进程: 运行中 (pid ${pid})`)
        : chalk.gray('  守护进程: 未运行 (weflow-cli assistant start)'))
    } catch { /* daemon 模块不可用时跳过 */ }
    console.log()
  })

// ==================== Interactive Menu (no arguments) ====================
async function showInteractiveMenu() {
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const configured = configService.isConfigured()
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const hasNtKey = !!configService.get('ntKey')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const hasLogin = !!configService.get('wechatOcToken')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const hasVault = !!configService.get('vaultRepo')

  // 首次运行：自动引导初始化
  if (!configured) {
    console.log(chalk.cyan('\n👋 欢迎使用 WeFlow CLI！'))
    console.log(chalk.gray('检测到尚未初始化，让我们开始设置...\n'))

    const { startInit } = await inquirer.prompt([{
      type: 'confirm',
      name: 'startInit',
      message: '是否现在开始初始化（检测微信数据目录并提取密钥）？',
      default: true,
    }])
    if (startInit) {
      const initCmd = program.commands.find(c => c.name() === 'init')
      if (initCmd) { await (initCmd as any).action(); return }
    }
    console.log(chalk.gray('\n稍后可运行: weflow-cli init\n'))
    return
  }

  // 构建菜单选项
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const choices: any[] = []

  choices.push(new inquirer.Separator('  📬 聊天记录'))
  choices.push({ name: '  查看会话列表', value: 'sessions' })
  choices.push({ name: '  查看聊天消息', value: 'messages' })
  choices.push({ name: '  导出聊天记录', value: 'export' })
  choices.push({ name: '  生成聊天月报', value: 'report' })

  choices.push(new inquirer.Separator('  📰 公众号日报'))
  choices.push({ name: '  生成今日日报', value: 'daily' })
  choices.push({ name: '  启动阅读器', value: 'daily-server' })

  choices.push(new inquirer.Separator('  💬 微信消息'))
  choices.push({
    name: `  扫码登录${hasLogin ? chalk.green(' (已登录)') : ''}`,
    value: 'login-wechat',
  })
  choices.push({
    name: `  监听消息${!hasLogin ? chalk.gray(' (需先登录)') : ''}`,
    value: 'listen',
    disabled: !hasLogin ? '请先扫码登录' : undefined,
  })
  choices.push({
    name: `  发送消息${!hasLogin ? chalk.gray(' (需先登录)') : ''}`,
    value: 'send',
    disabled: !hasLogin ? '请先扫码登录' : undefined,
  })

  const hasAiKey = !!configService.get('deepseekApiKey') || ['ollama', 'lmstudio'].includes(String(configService.get('aiEngine')))
  choices.push(new inquirer.Separator('  🧠 第二大脑 Agent'))
  choices.push({
    name: `  启动微信 AI 助手${hasLogin && hasAiKey ? '' : chalk.gray(' (需登录+API key)')}`,
    value: 'assistant-start',
    disabled: hasLogin && hasAiKey ? undefined : '需要消息通道登录和 AI 引擎配置',
  })
  choices.push({
    name: '  查看助手状态',
    value: 'assistant-status',
  })
  choices.push({
    name: '  停止助手',
    value: 'assistant-stop',
  })

  choices.push(new inquirer.Separator('  📚 知识库'))
  choices.push({ name: '  初始化 Obsidian Vault', value: 'vault-init' })
  choices.push({ name: '  编译概念图谱', value: 'wiki-compile' })
  choices.push({
    name: `  同步到远端${hasVault ? '' : chalk.gray(' (未配置仓库)')}`,
    value: 'vault-sync',
    disabled: !hasVault ? '请先配置 vaultRepo' : undefined,
  })

  choices.push(new inquirer.Separator('  📊 统计 & 报告'))
  choices.push({ name: '  📊 个人信息消费报告', value: 'chat-stats' })
  choices.push({ name: '  📚 微信读书统计', value: 'weread' })
  choices.push({ name: '  🧠 提取待办事项', value: 'extract-todos' })
  choices.push({ name: '  📝 AI 学习日报', value: 'generate-review' })

  choices.push(new inquirer.Separator('  ⚙️ 系统'))
  choices.push({ name: '  查看配置', value: 'config-show' })
  choices.push({ name: '  重新初始化', value: 'init' })

  // 显示状态摘要
  console.log(chalk.cyan('\n🚀 WeFlow CLI v1.5.0\n'))
  const statusParts: string[] = []
  statusParts.push(hasNtKey ? chalk.green('✓ 数据库') : chalk.yellow('○ 数据库'))
  statusParts.push(hasLogin ? chalk.green('✓ 消息通道') : chalk.gray('○ 消息通道'))
  statusParts.push(hasVault ? chalk.green('✓ Vault') : chalk.gray('○ Vault'))
  console.log(chalk.gray(`  状态: ${statusParts.join('  ')}\n`))

  const { action } = await inquirer.prompt([{
    type: 'select' as any,
    name: 'action',
    message: '请选择操作',
    choices,
    loop: false,
  }])

  // 辅助函数：执行命令
  const runCmd = async (name: string, opts: Record<string, any> = {}) => {
    const cmd = program.commands.find(c => c.name() === name)
    if (cmd) await (cmd as any).action(opts)
  }
  const runSubCmd = async (parent: string, child: string, opts: Record<string, any> = {}) => {
    const cmd = program.commands.find(c => c.name() === parent) as any
    const sub = cmd?.commands?.find((c: any) => c.name() === child)
    if (sub) await sub.action(opts)
  }
  const runPython = async (scriptName: string, label: string) => {
    const { execFile } = await import('child_process')
    const { promisify } = await import('util')
    const execFileAsync = promisify(execFile)
    const pkgRoot = resolvePackageRoot()
    const script = join(pkgRoot, 'scripts', scriptName)
    console.log(chalk.cyan(`正在${label}...\n`))
    try {
      const { stdout } = await execFileAsync(getPythonCommand(), [script], {
        timeout: 600_000, maxBuffer: 50 * 1024 * 1024,
        env: pythonProcessEnv(),
      })
      console.log(stdout)
    } catch (e: any) {
      console.error(chalk.red(safeSubprocessError(e, `${label}失败`)))
    }
  }

  switch (action) {
    case 'sessions':
      await runCmd('sessions')
      break
    case 'messages': {
      const sessions = await chatService.listSessions(undefined, 30)
      if (sessions.length === 0) { console.log(chalk.gray('无会话')); break }
      const { talker } = await inquirer.prompt([{
        type: 'select' as any, name: 'talker', message: '选择会话',
        choices: sessions.map((s, i) => ({
          name: `${String(i + 1).padStart(3)}. ${s.displayName || s.username}  ${chalk.gray((s.summary || '').slice(0, 30))}`,
          value: s.username,
        })),
        loop: false,
      }])
      await runCmd('messages', { talker, limit: '50', offset: '0' })
      break
    }
    case 'export': {
      const sessions = await chatService.listSessions(undefined, 30)
      if (sessions.length === 0) { console.log(chalk.gray('无会话')); break }
      const { talker } = await inquirer.prompt([{
        type: 'select' as any, name: 'talker', message: '选择要导出的会话',
        choices: sessions.map((s, i) => ({
          name: `${String(i + 1).padStart(3)}. ${s.displayName || s.username}`,
          value: s.username,
        })),
        loop: false,
      }])
      const { format } = await inquirer.prompt([{
        type: 'select' as any, name: 'format', message: '选择导出格式',
        choices: ['html', 'json', 'txt', 'excel'], loop: false,
      }])
      await runCmd('export', { talker, format, output: './output' })
      break
    }
    case 'report':
      await runCmd('report', {})
      break
    case 'daily': {
      const configKey = String(configService.get('deepseekApiKey') || '')
      const apiKey = process.env.DEEPSEEK_API_KEY || configKey
      if (!apiKey) {
        console.log(chalk.red('\n❌ 缺少 DeepSeek API key'))
        console.log(chalk.gray('  设置环境变量: set DEEPSEEK_API_KEY=<key>'))
        console.log(chalk.gray('  或持久化配置: weflow-cli config set deepseekApiKey <key>'))
        break
      }
      const now = new Date()
      const yyyy = now.getFullYear()
      const mm = String(now.getMonth() + 1).padStart(2, '0')
      const dd = String(now.getDate()).padStart(2, '0')
      const dateStr = `${yyyy}-${mm}-${dd}`
      const { spawn } = await import('child_process')
      const pipeline = join(resolvePackageRoot(), 'scripts', 'pipeline.py')
      console.log(chalk.cyan(`\n📰 正在生成 ${dateStr} 公众号日报...\n`))
      const child = spawn(getPythonCommand(), [pipeline, '--date', dateStr, '--interest', 'AI', '--skip-wiki'], {
        stdio: 'inherit',
        env: pythonProcessEnv(apiKey),
      })
      await new Promise<void>((resolve) => child.on('exit', () => resolve()))
      break
    }
    case 'daily-server': {
      const now = new Date()
      const yyyy = now.getFullYear()
      const mm = String(now.getMonth() + 1).padStart(2, '0')
      const dd = String(now.getDate()).padStart(2, '0')
      const dateStr = `${yyyy}-${mm}-${dd}`
      await runCmd('fav-server', { date: dateStr, port: '8765', open: true })
      break
    }
    case 'login-wechat':
      await runCmd('login-wechat', {})
      break
    case 'assistant-start':
      await runSubCmd('assistant', 'start')
      break
    case 'assistant-status':
      await runSubCmd('assistant', 'status')
      break
    case 'assistant-stop':
      await runSubCmd('assistant', 'stop')
      break
    case 'listen':
      await runCmd('listen', {})
      break
    case 'send': {
      const sessions = await chatService.listSessions(undefined, 30)
      if (sessions.length === 0) { console.log(chalk.gray('无会话')); break }
      const { talker } = await inquirer.prompt([{
        type: 'select' as any, name: 'talker', message: '选择接收人',
        choices: sessions.map((s, i) => ({
          name: `${String(i + 1).padStart(3)}. ${s.displayName || s.username}`,
          value: s.username,
        })),
        loop: false,
      }])
      const { message } = await inquirer.prompt([{
        type: 'input', name: 'message', message: '输入消息内容',
      }])
      if (!message.trim()) { console.log(chalk.gray('已取消')); break }
      await runCmd('send', { talker, message })
      break
    }
    case 'vault-init':
      await runSubCmd('vault', 'init', {})
      break
    case 'wiki-compile':
      await runSubCmd('wiki', 'compile', {})
      break
    case 'vault-sync':
      await runSubCmd('vault', 'sync', {})
      break
    case 'config-show':
      await runSubCmd('config', 'show')
      break
    case 'chat-stats':
      await runCmd('chat-stats', { period: 'week' })
      break
    case 'weread': {
      const wCmd = program.commands.find(c => c.name() === 'weread') as any
      const statsCmd = wCmd?.commands?.find((c: any) => c.name() === 'stats')
      if (statsCmd) await statsCmd.action({ mode: 'monthly' })
      break
    }
    case 'extract-todos': {
      const sessions = await chatService.listSessions(undefined, 30)
      if (sessions.length === 0) { console.log(chalk.gray('无会话')); break }
      const { talker } = await inquirer.prompt([{
        type: 'select' as any, name: 'talker', message: '选择联系人',
        choices: sessions.map((s, i) => ({
          name: `${String(i + 1).padStart(3)}. ${s.displayName || s.username}`,
          value: s.displayName || s.username,
        })),
        loop: false,
      }])
      await runCmd('todos', { talker, days: '7' })
      break
    }
    case 'generate-review':
      await runCmd('review', {})
      break
    case 'init':
      await runCmd('init')
      break
  }
}

// 检测是否无参数启动（显示交互式菜单）
const cliArgs = process.argv.slice(2)
if (cliArgs.length === 0) {
  showInteractiveMenu().catch((e) => {
    console.error(chalk.red(`\n错误: ${e.message}`))
    process.exit(1)
  })
} else {
  program.parse()
}
