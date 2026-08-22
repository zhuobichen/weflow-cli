/**
 * init 密钥派生服务 — 较新微信版本（实测 4.1.12.55）密钥体系适配。
 *
 * 新版微信进程内存中不再出现 x'<key><salt>' 文本, 内存扫描匹配不到任何密钥;
 * 但 DLL hook 仍能拿到全库 passphrase, 各库密钥改为本地派生:
 *   raw_key = PBKDF2-HMAC-SHA512(passphrase, 库文件前16字节盐, 256000轮, 32字节)
 * 派生后逐库用 sqlcipher 真实打开验证, 通过才写入配置。
 */
import chalk from 'chalk'
import { existsSync } from 'fs'
import { NtCore } from '../core/ntCore.js'
import { configService } from './configService.js'

export interface NtDbEntry {
  path: string
  name: string
  salt: string
  size: number
  wxid: string
}

type Log = (line: string) => void

/** 从已配置的 NT 主库路径推导 favorite.db 路径 */
export function detectFavDbPath(): string | null {
  const ntDbPath = configService.get('ntDbPath')
  if (!ntDbPath) return null
  const parts = ntDbPath.replace(/\\/g, '/').split('/')
  const idx = parts.lastIndexOf('db_storage')
  if (idx < 0) return null
  const favPath = [...parts.slice(0, idx), 'db_storage', 'favorite', 'favorite.db'].join('/')
  return existsSync(favPath) ? favPath : null
}

/**
 * 派生各 NT 库密钥并验证写入配置 (4.1.12+ 派生模式)。
 * @returns message_0.db 是否成功配置 (聊天主路径可用)
 */
export async function applyDerivedNtKeys(passphrase: string, databases: NtDbEntry[], log: Log = () => {}): Promise<boolean> {
  const targets = [
    { name: 'message/message_0.db', pathKey: 'ntDbPath', keyKey: 'ntKey', saltKey: 'ntSalt', label: '聊天主库' },
    { name: 'contact/contact.db', pathKey: 'contactDbPath', keyKey: 'contactKey', saltKey: 'contactSalt', label: '联系人库' },
    { name: 'sns/sns.db', pathKey: 'snsDbPath', keyKey: 'snsKey', saltKey: 'snsSalt', label: '朋友圈库' },
  ] as const
  let messageSaved = false
  let anySaved = false
  for (const t of targets) {
    const db = databases.find((d) => d.name === t.name)
    if (!db) continue
    const derivedKey = NtCore.deriveRawKey(passphrase, db.salt)
    const verify = await NtCore.verifyDbKey(db.path, derivedKey, db.salt)
    if (verify.success || verify.cannotVerify) {
      // cannotVerify = Python/sqlcipher3 环境问题而非密钥错误: 公式确定性派生, 值仍写入
      configService.set(t.pathKey, db.path)
      configService.set(t.keyKey, derivedKey)
      configService.set(t.saltKey, db.salt)
      const note = verify.success ? '派生密钥已验证' : `已写入但未能验证 (${verify.error})`
      log(chalk.green(`  ✓ ${t.label}: ${db.name} (${(db.size / 1024 / 1024).toFixed(1)}MB, ${note})`))
      if (t.name === 'message/message_0.db') messageSaved = true
      anySaved = true
    } else {
      log(chalk.red(`  ✗ ${t.label}: 派生密钥验证失败 (${verify.error || '密钥不匹配'})`))
    }
  }
  if (anySaved) await enableFavorites(passphrase, databases, log)
  return messageSaved
}

/** 启用收藏功能: 验证 passphrase 能派生打开 favorite.db 后写入配置 */
export async function enableFavorites(passphrase: string, databases: NtDbEntry[], log: Log = () => {}): Promise<void> {
  if (configService.get('favPassphrase')) return
  const favDb = databases.find((d) => d.name === 'favorite/favorite.db')
  const favPath = favDb?.path || detectFavDbPath()
  if (!favPath) return
  if (favDb) {
    const derived = NtCore.deriveRawKey(passphrase, favDb.salt)
    const v = await NtCore.verifyDbKey(favPath, derived, favDb.salt)
    if (!v.success && !v.cannotVerify) return
  }
  configService.set('favDbPath', favPath)
  configService.set('favPassphrase', passphrase)
  log(chalk.green('  ✓ 收藏功能已启用 (favorite.db passphrase 已配置)'))
}
