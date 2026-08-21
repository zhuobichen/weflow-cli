/**
 * 隐私关卡 — 本地/云端数据出境的唯一检查点。
 *
 * 原则:
 * - 数据库查询、记忆存储、工具执行全部在本机 (可信区)
 * - 只有经 redact() 处理后的文本才允许发往云端 LLM
 * - 本地推理引擎 (Ollama 等) 不出境, 跳过脱敏以保留完整能力
 *
 * 模式 (config: assistantPrivacy):
 * - open    不脱敏 (自担风险)
 * - balanced 默认; 工具输出中的 PII (电话/证件/邮箱/密钥/链接) 打码
 * - strict   balanced 基础上, 第三方聊天正文不出境 (仅保留时间/方向/类型)
 */
import { join } from 'path'
import { existsSync, mkdirSync, appendFileSync } from 'fs'
import os from 'os'
import { configService } from './configService.js'

export type PrivacyMode = 'open' | 'balanced' | 'strict'

const AUDIT_FILE = join(os.homedir(), '.weflow-cli', 'assistant_audit.log')

interface Rule { re: RegExp; mask: string }

const PII_RULES: Rule[] = [
  { re: /(?<!\d)1[3-9]\d{9}(?!\d)/g, mask: '[电话]' },
  { re: /(?<!\d)\d{17}[\dXx](?!\d)/g, mask: '[证件号]' },
  { re: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g, mask: '[邮箱]' },
  { re: /sk-[a-zA-Z0-9]{16,}/g, mask: '[密钥]' },
  { re: /https?:\/\/\S+/g, mask: '[链接]' },
]

export class PrivacyGate {
  mode(): PrivacyMode {
    const m = configService.get('assistantPrivacy')
    return m === 'open' || m === 'strict' ? m : 'balanced'
  }

  /** 本地引擎数据不出机器, 无需脱敏 */
  isLocalInference(): boolean {
    const engine = configService.get('aiEngine')
    return engine === 'ollama' || engine === 'lmstudio' || engine === 'local'
  }

  /**
   * 出境前脱敏。仅用于「工具从数据库挖出的第三方数据」;
   * 用户自己输入的话按原意发送 (用户对自己的话有处置权)。
   */
  redact(text: string): { safe: string; redactions: number } {
    if (this.mode() === 'open' || this.isLocalInference()) {
      return { safe: text, redactions: 0 }
    }
    let count = 0
    let safe = text
    for (const { re, mask } of PII_RULES) {
      safe = safe.replace(re, () => { count++; return mask })
    }
    return { safe, redactions: count }
  }

  /** strict 模式: 第三方聊天正文不出境, 只保留元数据形态 */
  maskMessageBody(body: string): string {
    if (this.mode() !== 'strict' || this.isLocalInference()) return body
    return `[内容${body.length}字已按严格模式屏蔽]`
  }

  /** 审计日志 — 只记事件与字节量, 绝不记内容 */
  audit(event: string, bytes = 0, extra = ''): void {
    try {
      const dir = join(os.homedir(), '.weflow-cli')
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
      const line = `[${new Date().toLocaleString('zh-CN')}] ${event}` +
        (bytes ? ` ${bytes}B` : '') + (extra ? ` ${extra}` : '')
      appendFileSync(AUDIT_FILE, line + '\n', 'utf8')
    } catch { /* 审计失败不阻断主流程 */ }
  }
}

export const privacyGate = new PrivacyGate()
