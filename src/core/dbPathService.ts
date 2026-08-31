import { join, basename, dirname } from 'path'
import { existsSync, readdirSync, statSync, lstatSync, readFileSync } from 'fs'
import { homedir } from 'os'
import { createDecipheriv } from 'crypto'
import { expandHomePath } from '../utils/pathUtils.js'

export interface WxidInfo {
  wxid: string
  modifiedTime: number
  nickname?: string
  avatarUrl?: string
}

export class DbPathService {
  /**
   * Validate a user-selected WeChat data location. WeChat's picker may expose
   * either the data root, an account folder, or that account's db_storage.
   */
  resolveDataRoot(inputPath: string): string | null {
    const rootPath = expandHomePath(inputPath)
    if (!rootPath || !existsSync(rootPath)) return null

    const ancestorPath = this.findAccountAncestor(rootPath)
    if (ancestorPath) return ancestorPath

    const descendantPath = this.findAccountDescendant(rootPath, 3)
    if (descendantPath) {
      return dirname(descendantPath)
    }

    return null
  }

  private findAccountAncestor(inputPath: string): string | null {
    let currentPath = inputPath
    for (let level = 0; level <= 4; level++) {
      if (this.isAccountDir(currentPath)) return currentPath
      const parentPath = dirname(currentPath)
      if (parentPath === currentPath) break
      currentPath = parentPath
    }
    return null
  }

  private findAccountDescendant(rootPath: string, maxDepth: number): string | null {
    if (maxDepth < 0) return null
    if (this.isAccountDir(rootPath)) return rootPath

    try {
      for (const entry of readdirSync(rootPath)) {
        const entryPath = join(rootPath, entry)
        let stat: ReturnType<typeof statSync>
        try {
          if (lstatSync(entryPath).isSymbolicLink()) continue
          stat = statSync(entryPath)
        } catch {
          continue
        }
        if (!stat.isDirectory()) continue
        if (this.shouldSkipSearchDirectory(entry)) continue
        const accountPath = this.findAccountDescendant(entryPath, maxDepth - 1)
        if (accountPath) return accountPath
      }
    } catch { }

    return null
  }

  private shouldSkipSearchDirectory(name: string): boolean {
    return [
      '$recycle.bin', 'node_modules', 'windows', 'program files',
      'program files (x86)', 'programdata', 'system volume information',
    ].includes(name.toLowerCase())
  }

  private findKnownDataRoots(basePath: string, maxDepth: number): string[] {
    const roots: string[] = []
    const visit = (currentPath: string, depth: number): void => {
      if (depth > maxDepth) return
      let entries: string[]
      try {
        entries = readdirSync(currentPath)
      } catch {
        return
      }

      for (const entry of entries) {
        const entryPath = join(currentPath, entry)
        try {
          if (lstatSync(entryPath).isSymbolicLink()) continue
        } catch {
          continue
        }
        if (!this.isDirectory(entryPath)) continue
        const lower = entry.toLowerCase()
        if (lower === 'xwechat_files' || lower === 'wechat files') {
          roots.push(entryPath)
          continue
        }
        if (depth < maxDepth && !this.shouldSkipSearchDirectory(lower) && lower !== 'appdata') {
          visit(entryPath, depth + 1)
        }
      }
    }
    if (this.isDirectory(basePath)) visit(basePath, 0)
    return roots
  }

  private readVarint(buf: Buffer, offset: number): { value: number, length: number } {
    let value = 0;
    let length = 0;
    let shift = 0;
    while (offset < buf.length && shift < 32) {
      const b = buf[offset++];
      value |= (b & 0x7f) << shift;
      length++;
      if ((b & 0x80) === 0) break;
      shift += 7;
    }
    return { value, length };
  }

  private extractMmkvString(buf: Buffer, keyName: string): string {
    const keyBuf = Buffer.from(keyName, 'utf8');
    const idx = buf.indexOf(keyBuf);
    if (idx === -1) return '';

    try {
      let offset = idx + keyBuf.length;
      const v1 = this.readVarint(buf, offset);
      offset += v1.length;
      const v2 = this.readVarint(buf, offset);
      offset += v2.length;

      // 合理性检查
      if (v2.value > 0 && v2.value <= 10000 && offset + v2.value <= buf.length) {
        return buf.toString('utf8', offset, offset + v2.value);
      }
    } catch { }
    return '';
  }

  private parseGlobalConfig(rootPath: string): { wxid: string, nickname: string, avatarUrl: string } | null {
    try {
      const configPath = join(rootPath, 'all_users', 'config', 'global_config');
      if (!existsSync(configPath)) return null;

      const fullData = readFileSync(configPath);
      if (fullData.length <= 4) return null;
      const encryptedData = fullData.subarray(4);

      const key = Buffer.alloc(16, 0);
      Buffer.from('xwechat_crypt_key').copy(key);   // 直接硬编码，iv更是不重要
      const iv = Buffer.alloc(16, 0);

      const decipher = createDecipheriv('aes-128-cfb', key, iv);
      decipher.setAutoPadding(false);
      const decrypted = Buffer.concat([decipher.update(encryptedData), decipher.final()]);

      const wxid = this.extractMmkvString(decrypted, 'mmkv_key_user_name');
      const nickname = this.extractMmkvString(decrypted, 'mmkv_key_nick_name');
      let avatarUrl = this.extractMmkvString(decrypted, 'mmkv_key_head_img_url');

      if (!avatarUrl && decrypted.includes('http')) {
        const httpIdx = decrypted.indexOf('http');
        const nullIdx = decrypted.indexOf(0x00, httpIdx);
        if (nullIdx !== -1) {
          avatarUrl = decrypted.toString('utf8', httpIdx, nullIdx);
        }
      }

      if (wxid || nickname) {
        return { wxid, nickname, avatarUrl };
      }
      return null;
    } catch (e) {
      console.error('解析 global_config 失败:', e);
      return null;
    }
  }


  /**
   * 自动检测微信数据库根目录
   */
  async autoDetect(options: { fullScan?: boolean } = {}): Promise<{ success: boolean; path?: string; error?: string }> {
    try {
      const possiblePaths: string[] = []
      const home = homedir()

      if (process.platform === 'darwin') {
        // macOS 微信 4.0.5+ 新路径（优先检测）
        const appSupportBase = join(home, 'Library', 'Containers', 'com.tencent.xinWeChat', 'Data', 'Library', 'Application Support', 'com.tencent.xinWeChat')
        if (existsSync(appSupportBase)) {
          try {
            const entries = readdirSync(appSupportBase)
            for (const entry of entries) {
              // 匹配形如 2.0b4.0.9 的版本目录
              if (/^\d+\.\d+b\d+\.\d+/.test(entry) || /^\d+\.\d+\.\d+/.test(entry)) {
                possiblePaths.push(join(appSupportBase, entry))
              }
            }
          } catch { }
        }
        // macOS 旧路径兜底
        possiblePaths.push(join(home, 'Library', 'Containers', 'com.tencent.xinWeChat', 'Data', 'Documents', 'xwechat_files'))
      } else if (process.platform === 'linux') {
        // Linux 原生微信 4.x：数据结构与 Windows 相同（xwechat_files），XDG 文档目录随 locale 变化
        possiblePaths.push(join(home, '.local', 'share', 'xwechat_files'))
        possiblePaths.push(join(home, 'xwechat_files'))
        possiblePaths.push(join(home, 'Documents', 'xwechat_files'))
        possiblePaths.push(join(home, '文档', 'xwechat_files'))
      } else {
        // Windows: 优先检测 NT 格式 (xwechat_files)
        possiblePaths.push(join(home, 'xwechat_files'))
        // Windows 微信4.x 数据目录 (Documents)
        possiblePaths.push(join(home, 'Documents', 'xwechat_files'))
        // Windows 微信3.x 数据目录
        possiblePaths.push(join(home, 'Documents', 'WeChat Files'))
        for (const basePath of [
          process.env.LOCALAPPDATA,
          process.env.APPDATA,
          process.env.USERPROFILE,
          process.env.OneDrive,
        ]) {
          if (basePath) possiblePaths.push(...this.findKnownDataRoots(basePath, 3))
        }
      }

      for (const path of possiblePaths) {
        const dataRoot = this.resolveDataRoot(path)
        if (dataRoot) {
          return { success: true, path: dataRoot }
        }
      }

      if (process.platform === 'win32') {
        for (const volumeRoot of this.getVolumeRoots()) {
          for (const dataRootPath of this.findKnownDataRoots(volumeRoot, 12)) {
            const dataRoot = this.resolveDataRoot(dataRootPath)
            if (dataRoot) return { success: true, path: dataRoot }
          }
        }
      }

      if (options.fullScan) {
        for (const volumeRoot of this.getVolumeRoots()) {
          const accountPath = this.findAccountDescendant(volumeRoot, 32)
          if (accountPath) return { success: true, path: dirname(accountPath) }
        }
      }

      return { success: false, error: '未能自动检测到微信数据库目录' }
    } catch (e) {
      return { success: false, error: String(e) }
    }
  }

  /**
   * 查找账号目录（包含 db_storage 或图片目录）
   */
  findAccountDirs(rootPath: string): string[] {
    const resolvedRootPath = expandHomePath(rootPath)
    const accounts: string[] = []

    try {
      const entries = readdirSync(resolvedRootPath)

      for (const entry of entries) {
        const entryPath = join(resolvedRootPath, entry)
        let stat: ReturnType<typeof statSync>
        try {
          stat = statSync(entryPath)
        } catch {
          continue
        }

        if (stat.isDirectory()) {
          if (!this.isPotentialAccountName(entry)) continue

          // 检查是否有有效账号目录结构
          if (this.isAccountDir(entryPath)) {
            accounts.push(entry)
          }
        }
      }
    } catch { }

    return accounts
  }

  private isAccountDir(entryPath: string): boolean {
    return (
      this.isDirectory(join(entryPath, 'db_storage')) ||  // WeChat 4.x
      this.isDirectory(join(entryPath, 'Msg')) ||          // WeChat 3.x
      this.isDirectory(join(entryPath, 'FileStorage', 'Image')) ||
      this.isDirectory(join(entryPath, 'FileStorage', 'Image2'))
    )
  }

  private getVolumeRoots(): string[] {
    if (process.platform !== 'win32') return [homedir()]
    const roots: string[] = []
    for (let code = 65; code <= 90; code++) {
      const volumeRoot = `${String.fromCharCode(code)}:\\`
      if (this.isDirectory(volumeRoot)) roots.push(volumeRoot)
    }
    return roots
  }

  private isDirectory(entryPath: string): boolean {
    try {
      return statSync(entryPath).isDirectory()
    } catch {
      return false
    }
  }

  private isPotentialAccountName(name: string): boolean {
    const lower = name.toLowerCase()
    if (lower.startsWith('all') || lower.startsWith('applet') || lower.startsWith('backup') || lower.startsWith('wmpf')) {
      return false
    }
    return true
  }

  private getAccountModifiedTime(entryPath: string): number {
    try {
      const accountStat = statSync(entryPath)
      let latest = accountStat.mtimeMs

      const dbPath = join(entryPath, 'db_storage')
      if (existsSync(dbPath)) {
        const dbStat = statSync(dbPath)
        latest = Math.max(latest, dbStat.mtimeMs)
      }

      const imagePath = join(entryPath, 'FileStorage', 'Image')
      if (existsSync(imagePath)) {
        const imageStat = statSync(imagePath)
        latest = Math.max(latest, imageStat.mtimeMs)
      }

      const image2Path = join(entryPath, 'FileStorage', 'Image2')
      if (existsSync(image2Path)) {
        const image2Stat = statSync(image2Path)
        latest = Math.max(latest, image2Stat.mtimeMs)
      }

      return latest
    } catch {
      return 0
    }
  }

  /**
   * 扫描目录名候选（仅包含下划线的文件夹，排除 all_users）
   */
  scanWxidCandidates(rootPath: string): WxidInfo[] {
    const resolvedRootPath = this.resolveDataRoot(rootPath) || expandHomePath(rootPath)
    const wxids: WxidInfo[] = []

    try {
      if (existsSync(resolvedRootPath)) {
        const entries = readdirSync(resolvedRootPath)
        for (const entry of entries) {
          const entryPath = join(resolvedRootPath, entry)
          let stat: ReturnType<typeof statSync>
          try { stat = statSync(entryPath) } catch { continue }
          if (!stat.isDirectory()) continue
          const lower = entry.toLowerCase()
          if (lower === 'all_users') continue
          if (!entry.includes('_')) continue
          wxids.push({ wxid: entry, modifiedTime: stat.mtimeMs })
        }
      }


      if (wxids.length === 0) {
        const rootName = basename(resolvedRootPath)
        if (rootName.includes('_') && rootName.toLowerCase() !== 'all_users') {
          const rootStat = statSync(resolvedRootPath)
          wxids.push({ wxid: rootName, modifiedTime: rootStat.mtimeMs })
        }
      }
    } catch { }

    const sorted = wxids.sort((a, b) => {
      if (b.modifiedTime !== a.modifiedTime) return b.modifiedTime - a.modifiedTime
      return a.wxid.localeCompare(b.wxid)
    });

    const globalInfo = this.parseGlobalConfig(resolvedRootPath);
    if (globalInfo) {
      for (const w of sorted) {
        if (w.wxid.startsWith(globalInfo.wxid) || sorted.length === 1) {
          w.nickname = globalInfo.nickname;
          w.avatarUrl = globalInfo.avatarUrl;
        }
      }
    }

    return sorted;
  }


  /**
   * 扫描 wxid 列表
   */
  scanWxids(rootPath: string): WxidInfo[] {
    const resolvedRootPath = this.resolveDataRoot(rootPath) || expandHomePath(rootPath)
    const wxids: WxidInfo[] = []

    try {
      if (this.isAccountDir(resolvedRootPath)) {
        const wxid = basename(resolvedRootPath)
        const modifiedTime = this.getAccountModifiedTime(resolvedRootPath)
        return [{ wxid, modifiedTime }]
      }

      const accounts = this.findAccountDirs(resolvedRootPath)

      for (const account of accounts) {
        const fullPath = join(resolvedRootPath, account)
        const modifiedTime = this.getAccountModifiedTime(fullPath)
        wxids.push({ wxid: account, modifiedTime })
      }
    } catch { }

    const sorted = wxids.sort((a, b) => {
      if (b.modifiedTime !== a.modifiedTime) return b.modifiedTime - a.modifiedTime
      return a.wxid.localeCompare(b.wxid)
    });

    const globalInfo = this.parseGlobalConfig(resolvedRootPath);
    if (globalInfo) {
      for (const w of sorted) {
        if (w.wxid.startsWith(globalInfo.wxid) || sorted.length === 1) {
          w.nickname = globalInfo.nickname;
          w.avatarUrl = globalInfo.avatarUrl;
        }
      }
    }
    return sorted;
  }

  /**
   * 获取默认数据库路径
   */
  getDefaultPath(): string {
    const home = homedir()
    if (process.platform === 'darwin') {
      // 优先返回 4.0.5+ 新路径
      const appSupportBase = join(home, 'Library', 'Containers', 'com.tencent.xinWeChat', 'Data', 'Library', 'Application Support', 'com.tencent.xinWeChat')
      if (existsSync(appSupportBase)) {
        try {
          const entries = readdirSync(appSupportBase)
          for (const entry of entries) {
            if (/^\d+\.\d+b\d+\.\d+/.test(entry) || /^\d+\.\d+\.\d+/.test(entry)) {
              const candidate = join(appSupportBase, entry)
              if (existsSync(candidate)) return candidate
            }
          }
        } catch { }
      }
      // 旧版本路径兜底
      return join(home, 'Library', 'Containers', 'com.tencent.xinWeChat', 'Data', 'Documents', 'xwechat_files')
    }
    if (process.platform === 'linux') {
      for (const p of [
        join(home, '.local', 'share', 'xwechat_files'),
        join(home, 'xwechat_files'),
        join(home, 'Documents', 'xwechat_files'),
        join(home, '文档', 'xwechat_files'),
      ]) {
        if (existsSync(p)) return p
      }
    }
    // 优先返回 4.x 路径
    const xwechatPath = join(home, 'Documents', 'xwechat_files')
    if (existsSync(xwechatPath)) return xwechatPath
    // 备用 4.x 路径（部分安装将数据放在用户目录下）
    const xwechatHomePath = join(home, 'xwechat_files')
    if (existsSync(xwechatHomePath)) return xwechatHomePath
    // 兜底 3.x 路径
    const wechatFilesPath = join(home, 'Documents', 'WeChat Files')
    if (existsSync(wechatFilesPath)) return wechatFilesPath
    return xwechatPath
  }
}

export const dbPathService = new DbPathService()
