#!/usr/bin/env node
import { Command } from 'commander'
import chalk from 'chalk'
import inquirer from 'inquirer'
import { join } from 'path'
import { existsSync, readFileSync } from 'fs'
import { homedir } from 'os'
import { dbPathService } from '../src/core/dbPathService.js'
import { keyService } from '../src/core/keyService.js'
import { NtCore } from '../src/core/ntCore.js'
import { configService } from '../src/services/configService.js'
import { chatService } from '../src/services/chatService.js'
import { exportService } from '../src/services/exportService.js'
import { resolveTalker as resolveTalkerCore } from '../src/utils/talkerUtils.js'
import { WechatMessageService } from '../src/services/wechatMessageService.js'
import { whitelistService } from '../src/services/whitelistService.js'
import type { ChatSession } from '../src/types.js'

const program = new Command()

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
async function resolveTalker(input: string): Promise<string> {
  try {
    const result = await resolveTalkerCore(input)
    // 显示解析结果（已知格式跳过打印）
    if (!input.startsWith('wxid_') && !input.includes('@chatroom') && !input.includes('@openim')) {
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
    console.log(chalk.red(`\n❌ ${e.message}\n`))
    process.exit(1)
  }
}

program
  .name('weflow-cli')
  .description('WeFlow CLI - 微信聊天记录命令行查询与导出工具')
  .version('1.0.0')

// ==================== init ====================
program
  .command('init')
  .description('自动检测微信数据目录并提取解密密钥')
  .action(async () => {
    console.log(chalk.cyan('🔧 WeFlow CLI 初始化\n'))

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
      console.log(chalk.white(`密钥: ${result.key.slice(0, 8)}...${result.key.slice(-8)}`))

    } else {
      // ====== 4.x 初始化 (原有逻辑) ======
      console.log(chalk.yellow('\n步骤 1/3: 检测微信数据目录...'))
      const detected = await dbPathService.autoDetect()
      if (!detected.success || !detected.path) {
        console.log(chalk.red('✗ 未检测到微信数据目录'))
        console.log(chalk.gray('  请确保微信 4.x 已安装并登录过'))
        console.log(chalk.gray('  或手动指定: weflow-cli config set dbPath <path>'))
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
      let extractedKey = tryReadWeFlowKey()

      if (extractedKey) {
        console.log(chalk.green('  ✓ 从 WeFlow 配置读取到密钥'))
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
        } else {
          console.log(chalk.red(`\n✗ 密钥提取失败: ${keyResult.error}`))
          console.log(chalk.gray('\n解决方案:'))
          console.log(chalk.gray('  1. 确保微信已登录'))
          console.log(chalk.gray('  2. 尝试在微信中发送一条消息'))
          console.log(chalk.gray('  3. 或手动指定密钥: weflow-cli config set decryptKey <64位密钥>'))
          process.exit(1)
        }
      }

      configService.set('decryptKey', extractedKey)
      console.log(chalk.green('\n✓ 密钥获取成功!'))

      // Step 4: 尝试扫描 NT 格式数据库 (xwechat_files)
      // 即使检测到传统路径，也可能存在 NT 格式数据库
      if (version) {
        console.log(chalk.yellow('\n步骤 4/4: 扫描 NT 格式数据库...'))
        console.log(chalk.gray('  正在从微信内存中匹配数据库密钥...'))

        const ntResult = await NtCore.scan()
        if (ntResult.success && ntResult.matched && ntResult.matched.length > 0) {
          console.log(chalk.green(`  ✓ 找到 ${ntResult.matched.length} 个 NT 数据库`))

          // 优先选择 message_0.db (主聊天数据库)
          let primaryDb = ntResult.matched.find((db: any) => db.name === 'message_0.db')
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

            // 显示所有匹配的数据库
            for (const db of ntResult.matched) {
              const marker = db === primaryDb || db === contactDb ? chalk.green('  → ') : '     '
              console.log(chalk.gray(`${marker}${db.name} (${(db.size / 1024 / 1024).toFixed(1)}MB)`))
            }
          }
        } else {
          console.log(chalk.gray(`  NT 数据库扫描: ${ntResult.error || '未找到匹配的数据库'}`))
          console.log(chalk.gray('  提示: 可以稍后运行 weflow-cli init 重新扫描'))
        }
      } else if (detected.path.toLowerCase().includes('xwechat_files')) {
        console.log(chalk.yellow('\n步骤 4/4: NT 格式数据库'))
        console.log(chalk.gray('  微信未运行，无法从内存中提取 NT 密钥'))
        console.log(chalk.gray('  请启动微信后运行: weflow-cli init'))
      }

      console.log(chalk.cyan('\n=============================='))
      console.log(chalk.cyan('初始化完成! (4.x 模式)'))
      console.log(chalk.cyan('=============================='))
      console.log(chalk.white(`数据目录: ${detected.path}`))
      console.log(chalk.white(`账号: ${selectedWxid.wxid}${selectedWxid.nickname ? ` (${selectedWxid.nickname})` : ''}`))
      console.log(chalk.white(`密钥: ${extractedKey.slice(0, 8)}...${extractedKey.slice(-8)}`))
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

configCmd
  .command('show')
  .description('显示当前配置')
  .action(() => {
    const config = configService.getAll()
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
  })

configCmd
  .command('set <key> <value>')
  .description('设置配置项 (dbPath, decryptKey, dbPath3x, decryptKey3x, dataVersion, wxid)')
  .action((key: string, value: string) => {
    const validKeys = ['dbPath', 'decryptKey', 'dbPath3x', 'decryptKey3x', 'dataVersion', 'wxid', 'ntDbPath', 'ntKey', 'ntSalt', 'contactDbPath', 'contactKey', 'contactSalt']
    if (!validKeys.includes(key)) {
      console.log(chalk.red(`无效的配置项: ${key}`))
      console.log(chalk.gray(`可用: ${validKeys.join(', ')}`))
      process.exit(1)
    }
    configService.set(key as any, value)
    console.log(chalk.green(`✓ 已设置 ${key}`))
  })

configCmd
  .command('clear')
  .description('清除所有配置')
  .action(() => {
    configService.clear()
    console.log(chalk.green('✓ 配置已清除'))
  })

// ==================== sessions ====================
program
  .command('sessions')
  .description('查看会话列表')
  .option('-k, --keyword <keyword>', '搜索关键词')
  .option('-n, --limit <number>', '最大数量', '30')
  .action(async (opts) => {
    if (!configService.isConfigured()) {
      console.log(chalk.red('请先运行 weflow-cli init'))
      process.exit(1)
    }

    const sessions = await chatService.listSessions(opts.keyword, parseInt(opts.limit))
    if (sessions.length === 0) {
      console.log(chalk.gray('未找到会话'))
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
  .action(async (talkerInput: string, opts) => {
    if (!configService.isConfigured()) {
      console.log(chalk.red('\n❌ 还没配置'))
      console.log(chalk.gray('  运行: weflow-cli init\n'))
      process.exit(1)
    }

    const talker = await resolveTalker(talkerInput)

    const messages = await chatService.getMessages(
      talker,
      parseInt(opts.limit),
      parseInt(opts.offset)
    )

    if (messages.length === 0) {
      console.log(chalk.gray('未找到消息'))
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

// ==================== contacts ====================
program
  .command('contacts')
  .description('查看联系人列表')
  .option('-k, --keyword <keyword>', '搜索关键词 (自动扩大搜索范围)')
  .option('-n, --limit <number>', '最大数量', '500')
  .action(async (opts) => {
    if (!configService.isConfigured()) {
      console.log(chalk.red('请先运行 weflow-cli init'))
      process.exit(1)
    }

    const contacts = await chatService.listContacts(opts.keyword, parseInt(opts.limit))
    if (contacts.length === 0) {
      console.log(chalk.gray('未找到联系人'))
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
  .option('-n, --limit <number>', '最大数量', '10000')
  .action(async (talkerInput: string, format: string, opts) => {
    if (!configService.isConfigured()) {
      console.log(chalk.red('\n❌ 还没配置'))
      console.log(chalk.gray('  运行: weflow-cli init\n'))
      process.exit(1)
    }

    const talker = await resolveTalker(talkerInput)

    const validFormats = ['json', 'txt', 'html', 'excel']
    if (!validFormats.includes(format)) {
      console.log(chalk.red(`无效格式: ${format}`))
      console.log(chalk.gray(`可用: ${validFormats.join(', ')}`))
      process.exit(1)
    }

    console.log(chalk.cyan(`正在导出 ${talker} 的聊天记录 (${format})...\n`))

    let result
    const limit = parseInt(opts.limit)

    switch (format) {
      case 'json':
        result = await exportService.exportJson(talker, opts.output, limit)
        break
      case 'txt':
        result = await exportService.exportTxt(talker, opts.output, limit)
        break
      case 'html':
        result = await exportService.exportHtml(talker, opts.output, limit)
        break
      case 'excel':
        result = await exportService.exportExcel(talker, opts.output, limit)
        break
    }

    if (result?.success) {
      console.log(chalk.green(`✓ 导出成功: ${result.path}`))
    } else {
      console.log(chalk.red(`✗ 导出失败: ${result?.error}`))
      process.exit(1)
    }
  })

// ==================== dbkey ====================
program
  .command('dbkey')
  .description('从运行中的微信进程提取数据库解密密钥 (自动检测版本)')
  .option('-t, --timeout <ms>', '超时时间(毫秒)', '60000')
  .action(async (opts) => {
    console.log(chalk.cyan('🔑 提取微信数据库密钥\n'))
    console.log(chalk.gray('请确保微信已登录且正在运行...\n'))

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
        console.log(chalk.green(`\n✓ 密钥: ${result.key}`))
        console.log(chalk.green(`  wxid: ${result.wxid}`))
        console.log(chalk.green(`  消息目录: ${result.msgDir}`))
      } else {
        console.log(chalk.red(`\n✗ 失败: ${result.error}`))
        process.exit(1)
      }
    } else {
      const result = await keyService.autoGetDbKey(parseInt(opts.timeout), (msg) => {
        console.log(chalk.gray(`  ${msg}`))
      })
      if (result.success && result.key) {
        console.log(chalk.green(`\n✓ 密钥: ${result.key}`))
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
  .action(async (opts) => {
    const path = opts.path || configService.get('dbPath') || dbPathService.getDefaultPath()
    console.log(chalk.cyan(`扫描目录: ${path}\n`))

    const wxids = dbPathService.scanWxidCandidates(path)
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
  .action(async (opts) => {
    const token = configService.get('wechatOcToken')
    if (token) {
      const { reconfirm } = await inquirer.prompt([{
        type: 'confirm',
        name: 'reconfirm',
        message: '已有登录状态，要重新登录吗？',
        default: false,
      }])
      if (!reconfirm) {
        console.log(chalk.gray('保持当前登录状态'))
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

      console.log(chalk.cyan('\n等待扫码...'))
      const session = await service.waitForLogin()

      if (session.status === 'confirmed') {
        console.log(chalk.green('\n✓ 登录成功!'))
        console.log(chalk.gray(`  account_id: ${session.accountId || '未知'}`))
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
  .action(() => {
    configService.set('wechatOcToken', '')
    configService.set('wechatOcAccountId', '')
    configService.set('wechatOcSyncBuf', '')
    console.log(chalk.green('✓ 已退出登录'))
  })

// ==================== whitelist ====================
const whitelistCmd = program
  .command('whitelist')
  .description('白名单管理 (不传参数显示列表)')

whitelistCmd
  .action(() => {
    const list = whitelistService.getList()
    if (list.length === 0) {
      console.log(chalk.gray('白名单为空'))
      console.log(chalk.gray('使用 weflow-cli whitelist add <昵称> 添加'))
      return
    }
    console.log(chalk.cyan(`白名单 (${list.length}):\n`))
    for (let i = 0; i < list.length; i++) {
      console.log(`  ${String(i + 1).padStart(2)}. ${list[i]}`)
    }
  })

whitelistCmd
  .command('add <target>')
  .description('添加白名单 (支持 wxid/昵称/备注名/序号)')
  .action(async (target: string) => {
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
        console.log(chalk.red(`✗ ${e.message}`))
        return
      }
    }

    // 检查是否已在名单中
    if (whitelistService.isAllowed(wxid)) {
      console.log(chalk.yellow(`"${displayName}" (${wxid}) 已在白名单中`))
      return
    }

    // 三重确认
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

    // 再确认一次（防止误操作）
    const { q2 } = await inquirer.prompt([{
      type: 'input',
      name: 'q2',
      message: `请输入 "确认" 以继续:`,
    }])
    if (q2 !== '确认') { console.log(chalk.gray('已取消')); return }

    whitelistService.addDirect(wxid)
    console.log(chalk.green(`\n✓ 已添加 "${displayName}" 到白名单`))
  })

whitelistCmd
  .command('rm <wxid>')
  .description('移除白名单中的 wxid')
  .action((wxid: string) => {
    if (whitelistService.remove(wxid)) {
      console.log(chalk.green(`✓ 已移除: ${wxid}`))
    } else {
      console.log(chalk.yellow(`未找到: ${wxid}`))
    }
  })

whitelistCmd
  .command('clear')
  .description('清空白名单')
  .action(async () => {
    const { confirm } = await inquirer.prompt([{
      type: 'confirm',
      name: 'confirm',
      message: '确定要清空所有白名单吗？',
      default: false,
    }])
    if (confirm) {
      whitelistService.clear()
      console.log(chalk.green('✓ 白名单已清空'))
    }
  })

// ==================== send ====================
program
  .command('send <target> <message>')
  .description('发送文本消息给指定联系人')
  .option('--image <path>', '发图片')
  .option('--file <path>', '发文件')
  .action(async (target: string, message: string, opts) => {
    // Resolve target
    let wxid: string
    try {
      wxid = await resolveTalker(target)
    } catch (e: any) {
      console.log(chalk.red(`${e.message}`))
      process.exit(1)
    }

    // Whitelist check
    if (!whitelistService.isAllowed(wxid)) {
      console.log(chalk.red('\n❌ 目标不在白名单中，拒绝发送'))
      console.log(chalk.gray(`  先运行: weflow-cli whitelist add ${target}`))
      console.log(chalk.gray(`  查看名单: weflow-cli whitelist\n`))
      process.exit(1)
    }

    // 二次确认
    const { confirm } = await inquirer.prompt([{
      type: 'confirm',
      name: 'confirm',
      message: `确认发送给 ${chalk.cyan(target)}？`,
      default: false,
    }])
    if (!confirm) {
      console.log(chalk.gray('已取消'))
      return
    }

    // Login check
    const token = configService.get('wechatOcToken')
    if (!token) {
      console.log(chalk.red('\n❌ 未登录消息通道'))
      console.log(chalk.gray('  先运行: weflow-cli login-wechat\n'))
      process.exit(1)
    }

    const service = new WechatMessageService({ token })

    let success = false
    if (opts.image) {
      success = await service.sendImage(wxid, opts.image)
    } else if (opts.file) {
      success = await service.sendFile(wxid, opts.file)
    } else {
      success = await service.sendText(wxid, message)
    }

    if (success) {
      console.log(chalk.green('✓ 发送成功'))
    } else {
      console.log(chalk.red('✗ 发送失败 — 可能需要先收到对方一条消息 (获取 context_token)'))
    }
  })

// ==================== listen ====================
program
  .command('listen')
  .description('监听微信消息 (Ctrl+C 退出)')
  .option('--target <wxid>', '只显示指定用户的消息')
  .action(async (opts) => {
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
    console.log(chalk.gray(`当前白名单: ${wl.join(', ')}`))

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
      console.log(chalk.gray(`[${time}]`) + ` ${chalk.blue(msg.senderNickname || msg.fromUserId)}:${kind} ${msg.messageStr}`)
    })

    // Graceful shutdown
    process.on('SIGINT', async () => {
      console.log(chalk.gray('\n正在停止监听...'))
      await service.stop()
      process.exit(0)
    })

    await service.startPolling()
  })

program.parse()
