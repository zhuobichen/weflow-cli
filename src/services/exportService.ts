import { join, basename } from 'path'
import { existsSync, mkdirSync, writeFileSync } from 'fs'
import { chatService } from './chatService.js'
import type { Message } from '../types.js'

export class ExportService {
  async exportJson(talker: string, outputDir: string, limit = 10000): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      const messages = await chatService.getMessages(talker, limit)
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

  async exportTxt(talker: string, outputDir: string, limit = 10000): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      const messages = await chatService.getMessages(talker, limit)
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

  async exportHtml(talker: string, outputDir: string, limit = 10000): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      const messages = await chatService.getMessages(talker, limit)
      if (messages.length === 0) {
        return { success: false, error: '未找到消息' }
      }

      const html = this.buildHtml(talker, messages)
      const dir = this.ensureOutputDir(outputDir)
      const filePath = join(dir, `${talker}_messages.html`)
      writeFileSync(filePath, html, 'utf8')
      return { success: true, path: filePath }
    } catch (e) {
      return { success: false, error: String(e) }
    }
  }

  async exportExcel(talker: string, outputDir: string, limit = 10000): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      const ExcelJS = await import('exceljs')
      const messages = await chatService.getMessages(talker, limit)
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
