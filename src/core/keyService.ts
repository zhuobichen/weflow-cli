import { join } from 'path'
import { existsSync, copyFileSync, mkdirSync } from 'fs'
import { execFile, execSync } from 'child_process'
import { promisify } from 'util'
import { createRequire } from 'module'
import os from 'os'
import type { DbKeyResult } from '../types.js'

const require = createRequire(import.meta.url)
const execFileAsync = promisify(execFile)

export class KeyService {
  private koffi: any = null
  private lib: any = null
  private initialized = false
  private initHook: any = null
  private pollKeyData: any = null
  private getStatusMessage: any = null
  private cleanupHook: any = null
  private getLastErrorMsg: any = null

  // Win32 APIs
  private kernel32: any = null
  private OpenProcess: any = null
  private CloseHandle: any = null
  private QueryFullProcessImageNameW: any = null

  private readonly PROCESS_ALL_ACCESS = 0x1F0FFF

  private getDllPath(): string {
    const archDir = process.arch === 'arm64' ? 'arm64' : 'x64'
    const candidates: string[] = []

    if (process.env.WX_KEY_DLL_PATH) {
      candidates.push(process.env.WX_KEY_DLL_PATH)
    }

    const cwd = process.cwd()
    candidates.push(join(cwd, 'resources', 'key', 'win32', archDir, 'wx_key.dll'))
    candidates.push(join(cwd, 'resources', 'key', 'win32', 'x64', 'wx_key.dll'))
    candidates.push(join(cwd, 'resources', 'key', 'win32', 'wx_key.dll'))
    candidates.push(join(cwd, 'resources', 'wx_key.dll'))

    for (const path of candidates) {
      if (existsSync(path)) return path
    }

    return candidates[0]
  }

  private isNetworkPath(path: string): boolean {
    return path.startsWith('\\\\')
  }

  private localizeNetworkDll(originalPath: string): string {
    try {
      const tempDir = join(os.tmpdir(), 'weflow_dll_cache')
      if (!existsSync(tempDir)) {
        mkdirSync(tempDir, { recursive: true })
      }
      const localPath = join(tempDir, 'wx_key.dll')
      if (existsSync(localPath)) return localPath
      copyFileSync(originalPath, localPath)
      return localPath
    } catch (e) {
      console.error('DLL 本地化失败:', e)
      return originalPath
    }
  }

  private ensureLoaded(): boolean {
    if (this.initialized) return true

    let dllPath = ''
    try {
      this.koffi = require('koffi')
      dllPath = this.getDllPath()

      if (!existsSync(dllPath)) {
        console.error(`wx_key.dll 不存在: ${dllPath}`)
        return false
      }

      if (this.isNetworkPath(dllPath)) {
        dllPath = this.localizeNetworkDll(dllPath)
      }

      this.lib = this.koffi.load(dllPath)
      this.initHook = this.lib.func('bool InitializeHook(uint32 targetPid)')
      this.pollKeyData = this.lib.func('bool PollKeyData(_Out_ char *keyBuffer, int bufferSize)')
      this.getStatusMessage = this.lib.func('bool GetStatusMessage(_Out_ char *msgBuffer, int bufferSize, _Out_ int *outLevel)')
      this.cleanupHook = this.lib.func('bool CleanupHook()')
      this.getLastErrorMsg = this.lib.func('const char* GetLastErrorMsg()')

      this.initialized = true
      return true
    } catch (e) {
      const errorMsg = e instanceof Error ? e.message : String(e)
      console.error(`加载 wx_key.dll 失败: ${dllPath} - ${errorMsg}`)
      return false
    }
  }

  private ensureKernel32(): boolean {
    if (this.kernel32) return true
    try {
      this.koffi = require('koffi')
      this.kernel32 = this.koffi.load('kernel32.dll')
      this.OpenProcess = this.kernel32.func('OpenProcess', 'void*', ['uint32', 'bool', 'uint32'])
      this.CloseHandle = this.kernel32.func('CloseHandle', 'bool', ['void*'])
      this.QueryFullProcessImageNameW = this.kernel32.func('QueryFullProcessImageNameW', 'bool', ['void*', 'uint32', this.koffi.out('uint16*'), this.koffi.out('uint32*')])
      return true
    } catch (e) {
      console.error('初始化 kernel32 失败:', e)
      return false
    }
  }

  private decodeUtf8(buf: Buffer): string {
    const nullIdx = buf.indexOf(0)
    return buf.toString('utf8', 0, nullIdx > -1 ? nullIdx : undefined).trim()
  }

  private decodeCString(ptr: any): string {
    try {
      if (typeof ptr === 'string') return ptr
      return this.koffi.decode(ptr, 'char', -1)
    } catch {
      return ''
    }
  }

  private async findPidByImageName(imageName: string): Promise<number | null> {
    try {
      const { stdout } = await execFileAsync('tasklist', ['/FI', `IMAGENAME eq ${imageName}`, '/FO', 'CSV', '/NH'])
      const lines = stdout.split(/\r?\n/).map((line) => line.trim()).filter(Boolean)
      for (const line of lines) {
        if (line.startsWith('INFO:')) continue
        const parts = line.split('","').map((p) => p.replace(/^"|"$/g, ''))
        if (parts[0]?.toLowerCase() === imageName.toLowerCase()) {
          const pid = Number(parts[1])
          if (!Number.isNaN(pid)) return pid
        }
      }
      return null
    } catch {
      return null
    }
  }

  async findWeChatPid(): Promise<number | null> {
    const names = ['Weixin.exe', 'WeChat.exe']
    for (const name of names) {
      const pid = await this.findPidByImageName(name)
      if (pid) return pid
    }
    return null
  }

  private async getProcessExecutablePath(pid: number): Promise<string | null> {
    if (!this.ensureKernel32()) return null
    const hProcess = this.OpenProcess(0x1000, false, pid)
    if (!hProcess) return null

    try {
      const sizeBuf = Buffer.alloc(4)
      sizeBuf.writeUInt32LE(1024, 0)
      const pathBuf = Buffer.alloc(1024 * 2)
      const ret = this.QueryFullProcessImageNameW(hProcess, 0, pathBuf, sizeBuf)
      if (ret) {
        const len = sizeBuf.readUInt32LE(0)
        return pathBuf.toString('ucs2', 0, len * 2)
      }
      return null
    } catch {
      return null
    } finally {
      this.CloseHandle(hProcess)
    }
  }

  /**
   * 等待微信进程出现，立即 hook 并提取密钥
   * 用户应在调用此方法后启动微信
   */
  async waitForKey(
    timeoutMs = 120_000,
    onStatus?: (message: string) => void
  ): Promise<DbKeyResult> {
    if (process.platform !== 'win32') {
      return { success: false, error: '自动提取 key 仅支持 Windows' }
    }
    if (!this.ensureLoaded()) {
      return { success: false, error: 'wx_key.dll 未加载，请确保 resources/key/ 目录存在' }
    }
    if (!this.ensureKernel32()) {
      return { success: false, error: 'Kernel32 初始化失败' }
    }

    const logs: string[] = []

    // 等待微信进程出现
    onStatus?.('等待微信进程出现...')
    let pid: number | null = null
    const waitStart = Date.now()
    while (Date.now() - waitStart < timeoutMs) {
      pid = await this.findWeChatPid()
      if (pid) break
      await new Promise(r => setTimeout(r, 500))
      process.stdout.write('.')
    }
    console.log('')

    if (!pid) {
      return { success: false, error: '等待超时，未检测到微信进程' }
    }

    onStatus?.(`检测到微信进程 (PID: ${pid})，立即 hook...`)
    return this.extractKeyFromPid(pid, logs, onStatus)
  }

  /**
   * 自动从运行中的微信进程提取数据库解密 key
   */
  async autoGetDbKey(
    timeoutMs = 60_000,
    onStatus?: (message: string) => void
  ): Promise<DbKeyResult> {
    if (process.platform !== 'win32') {
      return { success: false, error: '自动提取 key 仅支持 Windows' }
    }
    if (!this.ensureLoaded()) {
      return { success: false, error: 'wx_key.dll 未加载，请确保 resources/key/ 目录存在' }
    }
    if (!this.ensureKernel32()) {
      return { success: false, error: 'Kernel32 初始化失败' }
    }

    const logs: string[] = []

    onStatus?.('正在查找微信进程...')
    const pid = await this.findWeChatPid()
    if (!pid) {
      return { success: false, error: '未找到微信进程，请先启动微信并登录' }
    }

    onStatus?.(`检测到微信进程 (PID: ${pid})，正在提取 key...`)
    return this.extractKeyFromPid(pid, logs, onStatus)
  }

  private async extractKeyFromPid(
    pid: number,
    logs: string[],
    onStatus?: (message: string) => void,
    timeoutMs = 90_000
  ): Promise<DbKeyResult> {

    const ok = this.initHook(pid)
    if (!ok) {
      const error = this.getLastErrorMsg ? this.decodeCString(this.getLastErrorMsg()) : ''
      if (error) {
        if (error.includes('0xC0000022') || error.includes('ACCESS_DENIED')) {
          return { success: false, error: '权限不足，请以管理员身份运行 CLI' }
        }
        return { success: false, error }
      }
      return { success: false, error: '初始化 Hook 失败' }
    }

    const keyBuffer = Buffer.alloc(128)
    const start = Date.now()
    let pollCount = 0

    try {
      while (Date.now() - start < timeoutMs) {
        pollCount++
        const pollResult = this.pollKeyData(keyBuffer, keyBuffer.length)
        if (pollResult) {
          const key = this.decodeUtf8(keyBuffer)
          onStatus?.(`[调试] pollKeyData=true, key长度=${key.length}, 内容=${key.slice(0, 20)}...`)
          if (key.length === 64) {
            onStatus?.('密钥获取成功!')
            return { success: true, key, logs }
          }
        } else if (pollCount <= 5 || pollCount % 20 === 0) {
          // 前5次和每20次输出一次调试信息
          const hexSample = keyBuffer.slice(0, 16).toString('hex')
          onStatus?.(`[调试] poll #${pollCount}: pollResult=${pollResult}, buffer=${hexSample}`)
        }

        // 读取状态消息
        for (let i = 0; i < 5; i++) {
          const statusBuffer = Buffer.alloc(256)
          const levelOut = [0]
          if (!this.getStatusMessage(statusBuffer, statusBuffer.length, levelOut)) break
          const msg = this.decodeUtf8(statusBuffer)
          if (msg) {
            logs.push(msg)
            onStatus?.(msg)
          }
        }

        await new Promise(resolve => setTimeout(resolve, 120))
      }
    } finally {
      try { this.cleanupHook() } catch { }
    }

    return { success: false, error: '获取密钥超时，请确保微信已登录', logs }
  }

  /**
   * 检测当前运行的微信版本
   */
  async detectWeChatVersion(): Promise<'3.x' | '4.x' | null> {
    const pid3x = await this.findPidByImageName('WeChat.exe')
    if (pid3x) return '3.x'

    const pid4x = await this.findPidByImageName('Weixin.exe')
    if (pid4x) return '4.x'

    return null
  }

  /**
   * 提取 3.x 数据库密钥 (通过 Python/pywxdump 内存搜索)
   */
  async extract3xKey(
    onStatus?: (message: string) => void
  ): Promise<DbKeyResult & { wxid?: string; msgDir?: string; wxDir?: string }> {
    if (process.platform !== 'win32') {
      return { success: false, error: '3.x 密钥提取仅支持 Windows' }
    }

    onStatus?.('正在查找 3.x 微信进程...')

    const pid = await this.findPidByImageName('WeChat.exe')
    if (!pid) {
      return { success: false, error: '未找到 WeChat 3.x (WeChat.exe) 进程，请先启动 3.x 微信并登录' }
    }

    onStatus?.(`找到 3.x 进程 (PID: ${pid})，正在通过内存搜索提取密钥...`)

    try {
      const scriptPath = join(process.cwd(), 'scripts', 'extract_3x_key.py')

      if (!existsSync(scriptPath)) {
        return { success: false, error: `Python 脚本不存在: ${scriptPath}` }
      }

      const { stdout } = await execFileAsync('python', [scriptPath], {
        timeout: 120_000,
        maxBuffer: 1024 * 1024
      })

      // 过滤掉可能的警告信息，只取最后一行 JSON
      const lines = stdout.split('\n').filter((l: string) => l.trim().startsWith('{'))
      if (lines.length === 0) {
        return { success: false, error: `Python 脚本无有效输出: ${stdout.slice(0, 200)}` }
      }

      const result = JSON.parse(lines[lines.length - 1])

      if (result.success) {
        onStatus?.(`3.x 密钥提取成功! wxid=${result.wxid}`)
        return {
          success: true,
          key: result.key,
          wxid: result.wxid,
          msgDir: result.msg_dir,
          wxDir: result.wx_dir
        }
      } else {
        return { success: false, error: result.error || '3.x 密钥提取失败' }
      }
    } catch (e: any) {
      const msg = e?.message || String(e)
      if (msg.includes('ETIMEDOUT') || msg.includes('killed')) {
        return { success: false, error: '3.x 密钥提取超时' }
      }
      if (msg.includes('ENOENT') || msg.includes('python')) {
        return { success: false, error: '未找到 Python，请确保 Python 已安装并在 PATH 中' }
      }
      return { success: false, error: `3.x 密钥提取失败: ${msg}` }
    }
  }

  /**
   * 自动检测并提取密钥 (支持 3.x 和 4.x)
   */
  async autoExtractKey(
    onStatus?: (message: string) => void
  ): Promise<DbKeyResult & { version?: string; wxid?: string; msgDir?: string; wxDir?: string }> {
    const version = await this.detectWeChatVersion()

    if (version === '3.x') {
      onStatus?.('检测到 WeChat 3.x 版本，使用内存搜索方式提取密钥...')
      return this.extract3xKey(onStatus)
    } else if (version === '4.x') {
      onStatus?.('检测到 WeChat 4.x 版本，使用 Hook 方式提取密钥...')
      const result = await this.autoGetDbKey(60000, onStatus)
      return { ...result, version: '4.x' }
    } else {
      return { success: false, error: '未检测到微信进程 (WeChat.exe 或 Weixin.exe)' }
    }
  }
}

export const keyService = new KeyService()
