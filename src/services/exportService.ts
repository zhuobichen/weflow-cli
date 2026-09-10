import { join, basename, dirname } from 'path'
import { existsSync, mkdirSync, writeFileSync } from 'fs'
import { spawn } from 'child_process'
import { fileURLToPath } from 'url'
import { chatService } from './chatService.js'
import { configService } from './configService.js'
import type { Message } from '../types.js'

const __filename = fileURLToPath(import.meta.url)
const __dirname = dirname(__filename)

export class ExportService {
  async exportJson(talker: string, outputDir: string, limit = 0, from?: number, to?: number): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      const messages = this.filterByTime(await chatService.getMessages(talker, limit), from, to)
      if (messages.length === 0) {
        return { success: false, error: '未找到消息' }
      }

      const dir = this.ensureOutputDir(outputDir)
      const filePath = join(dir, `${talker}_messages.json`)
      writeFileSync(filePath, JSON.stringify(messages, null, 2), 'utf8')
      return { success: true, path: filePath }
    } catch (e) {
      return { success: false, error: String(e) }
    }
  }

  async exportTxt(talker: string, outputDir: string, limit = 0, from?: number, to?: number): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      const messages = this.filterByTime(await chatService.getMessages(talker, limit), from, to)
      if (messages.length === 0) {
        return { success: false, error: '未找到消息' }
      }

      const lines = messages.map(m => {
        const time = new Date(m.createTime * 1000).toLocaleString('zh-CN')
        const sender = m.isSend ? '我' : (m.senderUsername || talker)
        const content = m.parsedContent || m.rawContent || ''
        return `[${time}] ${sender}: ${content}`
      })

      const dir = this.ensureOutputDir(outputDir)
      const filePath = join(dir, `${talker}_messages.txt`)
      writeFileSync(filePath, lines.join('\n'), 'utf8')
      return { success: true, path: filePath }
    } catch (e) {
      return { success: false, error: String(e) }
    }
  }

  async exportHtml(talker: string, outputDir: string, limit = 0, from?: number, to?: number, noCoverFetch = false): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      if (from !== undefined || to !== undefined) {
        return this.exportHtmlBasic(talker, outputDir, limit, from, to)
      }
      // Use Python export_chat_html.py for rich HTML with images
      const cfg = configService.getAll()
      const db = cfg.ntDbPath
      const key = cfg.ntKey
      const salt = cfg.ntSalt

      if (!db || !key || !salt) {
        // Fallback: basic HTML
        return this.exportHtmlBasic(talker, outputDir, limit, from, to)
      }

      // Resolve Python script path (dist/src/services → package root)
      const pkgRoot = join(__dirname, '..', '..', '..')
      const script = join(pkgRoot, 'scripts', 'export_chat_html.py')

      // Cache dir for image thumbnails. WeChat 4.x keeps it next to db_storage
      // (<accountRoot>/cache), but older layouts nest it under db_storage, so
      // probe each candidate instead of assuming one.
      const cacheCandidates: string[] = []
      if (cfg.contactDbPath) {
        const accountRoot = dirname(dirname(dirname(cfg.contactDbPath)))
        cacheCandidates.push(join(accountRoot, 'cache'), join(accountRoot, 'db_storage', 'cache'))
      }
      if (cfg.ntDbPath) {
        const messageRoot = dirname(dirname(cfg.ntDbPath))
        cacheCandidates.push(join(messageRoot, 'cache'), join(messageRoot, 'db_storage', 'cache'))
      }
      const cacheDir = cacheCandidates.find((dir) => existsSync(dir)) || ''

      // Resolve display name for filename (prefer remark/nickname over wxid)
      let displayName = ''
      try {
        const sessions = await chatService.listSessions()
        const match = sessions.find(s => s.username === talker)
        if (match?.displayName && match.displayName !== talker) {
          displayName = match.displayName
        }
      } catch {}
      if (!displayName) {
        // Old conversations fall out of the session list long before they stop
        // being exportable, so fall back to the contact book.
        try {
          const contacts = await chatService.listContacts(talker)
          const contact = contacts.find(c => c.username === talker)
          const name = contact?.displayName || contact?.remark || contact?.nickname || ''
          if (name && name !== talker) {
            displayName = name
          }
        } catch {}
      }

      const args: string[] = [
        script,
        '--db', db,
        '--key', key,
        '--salt', salt,
        '--talker', talker,
        '--out', outputDir || `./output`,
        // Paginate: a whole year of chat in one file makes the browser crawl.
        '--per-page', '100',
      ]
      if (displayName) {
        args.push('--name', displayName)
      }
      if (cacheDir && existsSync(cacheDir)) {
        args.push('--cache-dir', cacheDir)
      }
      if (noCoverFetch) {
        args.push('--no-cover-fetch')
      }
      // WeChat 4.1.12.26+ derives every database's key from one master key, so
      // passing it lets the exporter merge all message shards instead of just
      // the single configured one.
      const masterKey = cfg.decryptKey
      if (masterKey) {
        args.push('--master-key', masterKey)
      }
      // Lets custom stickers be decrypted out of WeChat's local cache.
      if (cfg.emoticonSeed) {
        args.push('--emoticon-seed', cfg.emoticonSeed)
      }

      console.log(`  Exporting HTML via Python...`)
      const pythonResult = await this.runPythonExport(args)
      if (pythonResult.success && pythonResult.path) {
        return { success: true, path: pythonResult.path }
      }
      if (pythonResult.error) {
        console.error(`  Python export failed: ${pythonResult.error}`)
      }
      // Fallback to basic HTML
      return this.exportHtmlBasic(talker, outputDir, limit, from, to)
    } catch (e: any) {
      console.error(`  Python export failed: ${e.message}`)
      // Fallback to basic HTML
      return this.exportHtmlBasic(talker, outputDir, limit, from, to)
    }
  }

  /**
   * Run export_chat_html.py, relaying its progress lines live.
   *
   * The script can spend a while fetching article covers, so its output is
   * streamed rather than buffered - otherwise the user stares at nothing.
   */
  private runPythonExport(args: string[], timeoutMs = 600_000): Promise<{ success: boolean; path?: string; error?: string }> {
    return new Promise((resolve) => {
      const child = spawn('python', args, {
        windowsHide: true,
        // Otherwise Python writes the console code page and every Chinese
        // character in the relayed progress lines comes out as mojibake.
        env: { ...process.env, PYTHONIOENCODING: 'utf-8' },
      })
      let buffered = ''
      let stderrTail = ''
      let jsonLine = ''
      let settled = false
      let relayed = 0

      const finish = (r: { success: boolean; path?: string; error?: string }) => {
        if (settled) return
        settled = true
        clearTimeout(timer)
        resolve(r)
      }
      const timer = setTimeout(() => {
        child.kill()
        finish({ success: false, error: `timed out after ${Math.round(timeoutMs / 1000)}s` })
      }, timeoutMs)

      const handleLine = (line: string) => {
        if (!line) return
        // The final line is the machine-readable result; everything else is progress.
        if (line.startsWith('{') && line.endsWith('}')) {
          jsonLine = line
          return
        }
        if (relayed < 200) {
          relayed++
          console.log(`  ${line}`)
        }
      }

      child.stdout.on('data', (chunk: Buffer) => {
        buffered += chunk.toString()
        let idx: number
        while ((idx = buffered.indexOf('\n')) >= 0) {
          handleLine(buffered.slice(0, idx).trim())
          buffered = buffered.slice(idx + 1)
        }
      })
      child.stderr.on('data', (chunk: Buffer) => {
        stderrTail = (stderrTail + chunk.toString()).slice(-2000)
      })
      child.on('error', (err) => finish({ success: false, error: err.message }))
      child.on('close', () => {
        handleLine(buffered.trim())
        if (jsonLine) {
          try {
            const parsed = JSON.parse(jsonLine)
            if (parsed.success && parsed.files?.length > 0) {
              return finish({ success: true, path: parsed.files[0] })
            }
          } catch {}
        }
        finish({
          success: false,
          error: stderrTail.trim().split('\n').pop() || 'no output found',
        })
      })
    })
  }

  private async exportHtmlBasic(talker: string, outputDir: string, limit = 0, from?: number, to?: number): Promise<{ success: boolean; path?: string; error?: string }> {
    const messages = this.filterByTime(await chatService.getMessages(talker, limit), from, to)
    if (messages.length === 0) {
      return { success: false, error: '未找到消息' }
    }

    const html = this.buildHtml(talker, messages)
    const dir = this.ensureOutputDir(outputDir)
    const filePath = join(dir, `${talker}_messages.html`)
    writeFileSync(filePath, html, 'utf8')
    return { success: true, path: filePath }
  }

  async exportExcel(talker: string, outputDir: string, limit = 0, from?: number, to?: number): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      const ExcelJS = await import('exceljs')
      const messages = this.filterByTime(await chatService.getMessages(talker, limit), from, to)
      if (messages.length === 0) {
        return { success: false, error: '未找到消息' }
      }

      const workbook = new ExcelJS.Workbook()
      const sheet = workbook.addWorksheet('聊天记录')

      sheet.columns = [
        { header: '时间', key: 'time', width: 20 },
        { header: '发送者', key: 'sender', width: 15 },
        { header: '类型', key: 'type', width: 10 },
        { header: '内容', key: 'content', width: 80 },
        { header: '消息ID', key: 'msgId', width: 15 }
      ]

      for (const m of messages) {
        sheet.addRow({
          time: new Date(m.createTime * 1000).toLocaleString('zh-CN'),
          sender: m.isSend ? '我' : (m.senderUsername || talker),
          type: this.getMessageTypeName(m.localType),
          content: m.parsedContent || m.rawContent || '',
          msgId: m.localId
        })
      }

      const dir = this.ensureOutputDir(outputDir)
      const filePath = join(dir, `${talker}_messages.xlsx`)
      await workbook.xlsx.writeFile(filePath)
      return { success: true, path: filePath }
    } catch (e) {
      return { success: false, error: String(e) }
    }
  }

  private filterByTime(messages: Message[], from?: number, to?: number): Message[] {
    if (from === undefined && to === undefined) return messages
    return messages.filter((message) => {
      const timestamp = Number(message.createTime)
      return Number.isFinite(timestamp) &&
        (from === undefined || timestamp >= from) &&
        (to === undefined || timestamp <= to)
    })
  }

  private buildHtml(talker: string, messages: Message[]): string {
    const rows = messages.map(m => {
      const time = new Date(m.createTime * 1000).toLocaleString('zh-CN')
      const sender = m.isSend ? '我' : this.escapeHtml(m.senderUsername || talker)
      const contentType = m.localType
      const contentHtml = this.renderMessageContent(m)
      const align = m.isSend ? 'right' : 'left'
      const bgColor = m.isSend ? '#95ec69' : '#ffffff'

      return `<div style="text-align:${align};margin:8px 0;">
        <div style="display:inline-block;background:${bgColor};padding:8px 12px;border-radius:8px;max-width:80%;">
          <div style="font-size:12px;color:#999;">${sender} · ${time}</div>
          <div style="margin-top:4px;white-space:pre-wrap;word-break:break-all;">${contentHtml}</div>
        </div>
      </div>`
    }).join('\n')

    return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8">
  <title>聊天记录 - ${this.escapeHtml(talker)}</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f0f0f0; padding: 20px; }
    .container { max-width: 820px; margin: 0 auto; background: #f5f5f5; padding: 20px; border-radius: 12px; }
    .msg-image { color:#888; }
    .msg-image img { max-width:240px; max-height:240px; border-radius:4px; margin-top:6px; display:block; }
    .msg-app { margin:0; }
    .msg-app .app-title { font-size:14px; font-weight:600; color:#333; }
    .msg-app .app-desc { font-size:12px; color:#999; margin-top:2px; }
    .msg-app .app-url { display:block; margin-top:6px; padding:6px 10px; background:#f0f0f0; border-left:3px solid #07c160; text-decoration:none; color:#576b95; border-radius:0 4px 4px 0; font-size:13px; }
    .msg-app .app-file { color:#07c160; font-weight:600; }
    .msg-sys { color:#bbb; font-size:13px; }
    .msg-media { color:#888; }
  </style>
</head>
<body>
  <div class="container">
    <h2 style="text-align:center;">聊天记录 - ${this.escapeHtml(talker)}</h2>
    <p style="text-align:center;color:#999;">共 ${messages.length} 条消息</p>
    ${rows}
  </div>
</body>
</html>`
  }

  private renderMessageContent(m: Message): string {
    switch (m.localType) {
      case 1:
        return this.escapeHtml(m.parsedContent || m.rawContent || '')

      case 3: {
        let html = `<span class="msg-media">[图片]</span>`
        if (m.imageFileName) {
          html = `<span class="msg-media">[图片: ${this.escapeHtml(m.imageFileName)}]</span>`
        }
        if (m.imageBase64) {
          html += `<br><img src="data:image/jpeg;base64,${m.imageBase64}" />`
        }
        return html
      }

      case 34:
        return '<span class="msg-media">[语音]</span>'

      case 43:
        return '<span class="msg-media">[视频]</span>'

      case 47:
        return this.escapeHtml(m.parsedContent || '[表情]')

      case 49: {
        let html = '<div class="msg-app">'
        if (m.appTitle) {
          html += `<div class="app-title">${this.escapeHtml(m.appTitle)}</div>`
        }
        if (m.appDescription) {
          html += `<div class="app-desc">${this.escapeHtml(m.appDescription)}</div>`
        }
        if (m.appUrl) {
          html += `<a class="app-url" href="${this.escapeHtml(m.appUrl)}" target="_blank">${this.escapeHtml(m.appUrl)}</a>`
        }
        html += '</div>'
        if (!m.appTitle && !m.appDescription && !m.appUrl) {
          html = this.escapeHtml(m.parsedContent || '[链接/文件]')
        }
        return html
      }

      case 50:
        return '<span class="msg-media">[语音通话]</span>'

      case 10000:
        return `<span class="msg-sys">${this.escapeHtml(m.parsedContent || '')}</span>`

      default:
        return this.escapeHtml(m.parsedContent || m.rawContent || '')
    }
  }

  private escapeHtml(str: string): string {
    return str
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
  }

  private getMessageTypeName(type: number): string {
    const map: Record<number, string> = {
      1: '文本', 3: '图片', 34: '语音', 42: '名片',
      43: '视频', 47: '表情', 48: '位置', 49: '链接/文件',
      50: '语音通话', 10000: '系统', 10002: '引用'
    }
    return map[type] || `类型${type}`
  }

  private ensureOutputDir(dir: string): string {
    const resolved = dir || './output'
    if (!existsSync(resolved)) {
      mkdirSync(resolved, { recursive: true })
    }
    return resolved
  }
}

export const exportService = new ExportService()
