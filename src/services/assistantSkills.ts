/**
 * 助手能用的技能包（Skills）—— 发现、解析、四态降级、正文读取。
 *
 * **技能是内容，不是代码**：本模块只把 `SKILL.md` 当**资料**交给模型（给路径、给正文），
 * 不执行其中任何东西、不给模型增加任何新工具。这是 P4「插件/适配器机制」验收标准的
 * 保守落地：「第三方扩展无法直接读取配置、数据库或任意文件；扩展能被单独禁用」。
 * 想给助手加能力要走 `docs/EXTENDING.md` 的 Recipe A（加工具），不是往技能里塞脚本。
 *
 * 口径来源：技能格式与 Anthropic Agent Skills 一致（`name` + `description` frontmatter），
 * 我们只**读**它，不发明新格式 —— 这样用户已经装在 `~/.claude/skills` 下的技能直接可用。
 *
 * 三个实测得来的硬约束（别按直觉改）：
 * 1. **只解析文件开头那一段 `---`**。实测用户的技能正文里也有 `categories:`/`created:`/
 *    `tags:` 这些行（`memory-organizer`、`obsidian-wiki-workflow`），扫全文会把它们当字段读进来。
 * 2. **必须处理 YAML 块标量**。真实技能里 `description: >-` / `description: |` 后面跟着缩进的
 *    多行正文，不处理的话 description 就变成 `>-` 两个字。
 * 3. **ID 规范要比 Anthropic 宽**。他们的 `^[a-z0-9]+(-[a-z0-9]+)*$` 会把用户自己的
 *    `clz_docx_to_mp`、`agent-dialog_management`、`ylx_onehub_usage_monitor` 判为不合规 ——
 *    那些是真实在用的技能，所以不合规只**警告**，不丢弃。
 */
import { existsSync, readdirSync, readFileSync, statSync } from 'fs'
import { join, basename } from 'path'
import { homedir } from 'os'
import { configService } from './configService.js'
import { expandHomePath } from '../utils/pathUtils.js'

/** 技能正文一次最多交给模型多少字（`read_skill`）：足够长，又不至于把上下文吃掉 */
export const SKILL_BODY_LIMIT = 8192

/**
 * 我们接受的 ID：小写字母、数字、点、下划线、连字符 —— 比 Anthropic 规范宽（见文件头第 3 条）。
 */
const ID_RE = /^[a-z0-9][a-z0-9._-]*$/
/**
 * Anthropic Agent Skills 的严格规范。**只用来说话，不用来拒绝**：
 * 不合规的 ID 照样能用（用户的 `clz_docx_to_mp` 就在用），但 `skill check` 要讲出来，
 * 免得他以为这些能直接搬去别的 Agent 用。
 */
const SPEC_ID_RE = /^[a-z0-9]+(-[a-z0-9]+)*$/

export interface SkillInfo {
  /** 目录名，也是引用时用的 id */
  id: string
  /** 显示名：frontmatter 的 `name`，缺了就用目录名 */
  name: string
  description: string
  version: string
  /** `SKILL.md` 的绝对路径 —— 交给模型的就是它 */
  file: string
  /** 来自哪个根目录（同 id 冲突时靠前的胜出） */
  root: string
  /** 技能自己声明的 `enabled: false` */
  selfDisabled: boolean
  /** `triggers:` 列表，`list_skills --query` 会匹配它 */
  triggers: string[]
  /** 不合规 / 缺字段之类的提醒，只报不改 */
  notes: string[]
}

export interface SkillScan {
  skills: SkillInfo[]
  /** 读不出 frontmatter 的 `SKILL.md`：路径 + 原因。**不静默跳过** */
  unreadable: Array<{ file: string; reason: string }>
  /** 同 id 出现在多个根：只报冲突，不覆盖 */
  conflicts: Array<{ id: string; winner: string; loser: string }>
  /** 根本没读到的根目录（配错了会表现为"一个技能都没有"） */
  missingRoots: string[]
}

export type SkillState = 'ready' | 'disabled' | 'missing' | 'unknown'

// ---------- 根目录 ----------

/**
 * 技能根目录清单。优先级：环境变量 > 配置键 > 默认两个根。
 *
 * 默认两个根是有意的：`~/.claude/skills` 让用户**已有的**技能立刻可用，
 * `~/.weflow-cli/skills` 是本项目自己的落脚点（现在多半是空的）。
 *
 * 注意 `configService.get()` 只展开**开头**那个 `~`，逗号分隔的清单里第二个 `~` 它不管，
 * 所以这里逐段自己调 `expandHomePath`（踩过：不展开会让第二个根静默不存在）。
 */
export function skillRoots(): string[] {
  const env = String(process.env.WEFLOW_ASSISTANT_SKILL_DIRS || '').trim()
  const raw = env || String(configService.get('skillDirs') || '').trim()
  if (raw) {
    return raw.split(/[,;]/)
      .map(part => expandHomePath(part.trim().replace(/^["']|["']$/g, '')))
      .filter(Boolean)
  }
  return [join(homedir(), '.claude', 'skills'), join(homedir(), '.weflow-cli', 'skills')]
}

/** 配置键 `skillDisabled`：用户侧的禁用名单（技能自己也能声明 `enabled: false`） */
export function disabledIds(): string[] {
  const raw = String(configService.get('skillDisabled') || '').trim()
  if (!raw) return []
  return raw.split(/[,;]/).map(part => part.trim()).filter(Boolean)
}

// ---------- frontmatter ----------

interface ParsedFrontmatter {
  fields: Record<string, string>
  lists: Record<string, string[]>
}

/**
 * 只解析开头那一段 `---`。返回 null 表示**这一段不成立**（不是"没有字段"）——
 * 调用方要把这种情况报出来，不要当成"这个技能没写描述"。
 */
export function parseFrontmatter(text: string): ParsedFrontmatter | null {
  const t = String(text || '').replace(/^﻿/, '')
  const lines = t.split(/\r?\n/)
  if (!/^---[ \t]*$/.test(lines[0] || '')) return null
  let end = -1
  for (let i = 1; i < lines.length; i += 1) {
    if (/^---[ \t]*$/.test(lines[i])) { end = i; break }
  }
  if (end === -1) return null   // 开头有 --- 却没有收尾：残缺，宁可报错也不猜

  const fields: Record<string, string> = {}
  const lists: Record<string, string[]> = {}
  let blockKey: string | null = null     // 正在收的块标量（`description: >-` 这类）
  let blockMode: 'folded' | 'literal' = 'folded'
  let blockLines: string[] = []
  let listKey: string | null = null      // 正在收的列表（`triggers:` 这类）

  const closeBlock = () => {
    if (!blockKey) return
    // `>` 折行（空格连接），`|` 保留换行。`-` 后缀（`>-`/`|-`）表示去掉末尾换行，这里一并处理。
    fields[blockKey] = (blockMode === 'literal' ? blockLines.join('\n') : blockLines.join(' ')).trim()
    blockKey = null
    blockLines = []
  }
  const closeList = () => {
    if (listKey && lists[listKey]?.length === 0) lists[listKey] = []
    listKey = null
  }

  for (let i = 1; i < end; i += 1) {
    const raw = lines[i]
    const line = raw.replace(/\s+$/, '')
    if (!line.trim() || /^\s*#/.test(line)) continue

    const indented = /^\s/.test(line)
    if (indented) {
      const item = /^\s+-\s*(.*)$/.exec(line)
      if (item && listKey) { lists[listKey].push(stripQuotes(item[1].trim())); continue }
      if (blockKey) { blockLines.push(line.replace(/^\s+/, '')); continue }
      continue   // 嵌套映射（如 `metadata:` 下缩进的 `type: skill`）——只认扁平的，嵌套的忽略
    }

    closeBlock()
    closeList()

    const m = /^([A-Za-z][A-Za-z0-9_.-]*)\s*:\s*(.*)$/.exec(line)
    if (!m) continue
    const key = m[1]
    const value = m[2].trim()

    if (/^[|>][-+]?$/.test(value)) {
      blockKey = key
      blockMode = value.startsWith('|') ? 'literal' : 'folded'
      continue
    }
    if (value === '') {
      // 空值：后面若是缩进的 `- ` 就是列表，否则就是个空字段
      lists[key] = lists[key] || []
      listKey = key
      fields[key] = ''
      continue
    }
    fields[key] = stripQuotes(value)
  }
  closeBlock()
  closeList()
  return { fields, lists }
}

function stripQuotes(value: string): string {
  return String(value || '').replace(/^["']|["']$/g, '')
}

// ---------- 扫描 ----------

export function scanSkills(roots = skillRoots()): SkillScan {
  const skills: SkillInfo[] = []
  const unreadable: SkillScan['unreadable'] = []
  const conflicts: SkillScan['conflicts'] = []
  const missingRoots: string[] = []
  const seen = new Map<string, string>()

  /**
   * 递归找一个目录下的 `SKILL.md`（2026-11-24，读 pi 的技能发现后补的）。
   *
   * 原来只认 `<root>/<id>/SKILL.md` **一层**。用户按主题分目录
   * （`skills/写作/公众号/SKILL.md`）时，**全部扫不到，而且不报错**——
   * 他只会觉得"我的技能怎么不见了"。这与本仓反复强调的"静默失败最坑"是同一类。
   *
   * 递归规则：
   * - 深度上限 4 层（防止误把整个家目录扫一遍）
   * - 遇到 `SKILL.md` 就收下，**不再往下钻**（一个技能内部不会再有技能）
   * - 跳过 `.` 开头的目录和 `node_modules`
   * - **有子目录却没有 SKILL.md 的，记进 `unreadable`**：不是错误，但要让用户看见
   *   "这个目录我扫过了，里面没有技能"——否则又是静默。
   */
  const walk = (dir: string, depth: number, depthLimit = 4): void => {
    if (depth > depthLimit) return
    let entries: string[] = []
    try {
      entries = readdirSync(dir)
    } catch (error) {
      // **目录读不了要说出来**（2026-11-24）。原来这里是裸 `return`——
      // 权限不足、路径是个坏软链、目录被删，三种情况在结果里一模一样：
      // "这个目录下没有技能"。用户只会觉得自己的技能不见了。
      // 递归之后误踩这种目录的概率更高（多一层就多一种失败），必须报。
      unreadable.push({ file: dir, reason: `目录读不了：${(error as Error).message}` })
      return
    }
    for (const entry of entries) {
      if (entry.startsWith('.') || entry === 'node_modules') continue
      const full = join(dir, entry)
      let isDir = false
      try { isDir = statSync(full).isDirectory() } catch { continue }
      if (!isDir) continue
      const file = join(full, 'SKILL.md')
      let hasSkill = false
      try { hasSkill = existsSync(file) && statSync(file).isFile() } catch { hasSkill = false }
      if (hasSkill) {
        collectSkill(full, file)
        continue   // 有技能就不再往下钻
      }
      walk(full, depth + 1, depthLimit)
    }
  }

  const collectSkill = (dir: string, file: string): void => {
      let text = ''
      try {
        text = readFileSync(file, 'utf8')
      } catch (error) {
        unreadable.push({ file, reason: `读不了：${(error as Error).message}` })
        return
      }
      const fm = parseFrontmatter(text)
      if (!fm) {
        unreadable.push({ file, reason: '开头的 --- frontmatter 段缺失或不闭合' })
        return
      }
      const notes: string[] = []
      const id = basename(dir)
      if (!ID_RE.test(id)) {
        // 连我们这套宽规则都不满足（大写、空格、中文…）：仍然保留，但要说出来
        notes.push('ID 含非规范字符（不是小写字母/数字/点/下划线/连字符），本机可用，但别的 Agent 可能不认')
      } else if (!SPEC_ID_RE.test(id)) {
        // 实测用户有 3 个这种 ID（clz_docx_to_mp 等）。可用，但搬去别的 Agent 会不合规。
        notes.push('ID 不符合 Anthropic Agent Skills 规范（只允许小写字母/数字/单个连字符），本机可用，搬去别的 Agent 可能不认')
      }
      const description = String(fm.fields.description || '').trim()
      if (!description) notes.push('frontmatter 里没有 description —— 模型只能靠它判断这个技能是干什么的')

      if (seen.has(id)) {
        conflicts.push({ id, winner: seen.get(id)!, loser: file })
        return   // 靠前的根胜出；后一个只报冲突，不覆盖
      }
      seen.set(id, file)
      skills.push({
        id,
        name: String(fm.fields.name || id).trim() || id,
        description,
        version: String(fm.fields.version || '').trim(),
        file,
        // 递归后条目可能在深层，`root` 要取"包含它的那个技能根"，否则展示路径会错
        root: roots.find((r) => dir.startsWith(r)) ?? dir,
        selfDisabled: String(fm.fields.enabled || '').trim().toLowerCase() === 'false',
        triggers: fm.lists.triggers || [],
        notes,
      })
  }

  for (const root of roots) {
    let isDir = false
    try { isDir = existsSync(root) && statSync(root).isDirectory() } catch { isDir = false }
    if (!isDir) { missingRoots.push(root); continue }
    walk(root, 1)
  }

  skills.sort((a, b) => a.id.localeCompare(b.id))
  return { skills, unreadable, conflicts, missingRoots }
}

export function findSkill(id: string, scan: SkillScan = scanSkills()): SkillInfo | undefined {
  return scan.skills.find(s => s.id === id)
}

// ---------- 四态降级 ----------

export function skillState(skill: SkillInfo, disabled = disabledIds()): SkillState {
  if (skill.selfDisabled || disabled.includes(skill.id)) return 'disabled'
  return 'ready'
}

/**
 * 把技能渲染成给模型的一句话。四态，每一态都**说实话**：
 * 缺文件不阻断（照 WeChatBridge 的做法：继续做，但要求模型在回答里说明这块没做）。
 */
export function describeSkill(id: string, scan: SkillScan = scanSkills()): { state: SkillState; line: string } {
  const skill = findSkill(id, scan)
  if (!skill) {
    return {
      state: 'missing',
      line: `技能「${id}」不可用（本机没有这个技能），请直接完成，并在回答里说明哪部分没做。`,
    }
  }
  const state = skillState(skill)
  if (state === 'disabled') {
    return { state, line: `技能「${skill.name}」已被用户禁用，不要使用它。` }
  }
  return {
    state,
    line: `技能「${skill.name}」：说明文件在 \`${skill.file}\`，请先读它再严格按其执行。`,
  }
}

/**
 * 解析 `{{skill:<id>}}` 行内引用。**只认这一种形式**，别的 `{{...}}` 原样保留
 * （本仓没有模板引擎，不要让人以为这是通用的）。
 * 未知 id **不静默删掉** —— 显式告诉模型"你引用了不存在的技能"。
 */
export function renderSkillRefs(text: string, scan: SkillScan = scanSkills()): string {
  return String(text || '').replace(/\{\{skill:([^}]+)\}\}/g, (_all, rawId: string) => {
    const id = rawId.trim()
    const { line } = describeSkill(id, scan)
    return line
  })
}

/** 一个场景/一段文本实际引用了哪些技能：行内引用 ∪ 显式声明（去重、保序） */
export function referencedSkillIds(text: string, declared: string[] = []): string[] {
  const ids: string[] = []
  const push = (id: string) => { if (id && !ids.includes(id)) ids.push(id) }
  for (const m of String(text || '').matchAll(/\{\{skill:([^}]+)\}\}/g)) push(m[1].trim())
  for (const id of declared) push(String(id || '').trim())
  return ids
}

// ---------- 正文读取（给工具用，路径限界是硬要求） ----------

export interface SkillBody { ok: boolean; text: string; truncated?: boolean }

/**
 * 读某个技能的 `SKILL.md` 正文。
 *
 * **路径限界**：只从扫描结果里取路径，绝不接受调用方拼出来的路径。也就是说
 * `read_skill('../config.json')` 这类根本走不到文件系统 —— 因为 `../config.json`
 * 不是任何一个扫描出来的技能 id。这条是 P4 验收标准的现场，有测试逐个盯着。
 */
export function readSkillBody(id: string, scan: SkillScan = scanSkills()): SkillBody {
  const skill = findSkill(id, scan)
  if (!skill) return { ok: false, text: `本机没有技能「${id}」。可以用 list_skills 看有哪些。` }
  if (skillState(skill) === 'disabled') {
    return { ok: false, text: `技能「${skill.id}」已被禁用，不读取它的内容。` }
  }
  let text = ''
  try {
    text = readFileSync(skill.file, 'utf8')
  } catch (error) {
    return { ok: false, text: `技能「${skill.id}」的说明文件读不了：${(error as Error).message}` }
  }
  const truncated = text.length > SKILL_BODY_LIMIT
  return {
    ok: true,
    text: truncated ? `${text.slice(0, SKILL_BODY_LIMIT)}\n\n…（正文过长，已截断到 ${SKILL_BODY_LIMIT} 字）` : text,
    truncated,
  }
}

// ---------- 提示词目录 ----------

/** 只列名字 + 描述 + 状态。27 个技能的正文全塞进每轮提示词是浪费，正文按需 `read_skill`。 */
export function skillCatalogueLines(scan: SkillScan = scanSkills(), limit = 40): string[] {
  const disabled = disabledIds()
  const usable = scan.skills.filter(s => skillState(s, disabled) === 'ready')
  const lines = usable.slice(0, limit).map(s => {
    const desc = s.description.length > 80 ? `${s.description.slice(0, 80)}…` : s.description
    return `· ${s.id}（${s.name}）：${desc || '（无描述）'}`
  })
  const hidden = usable.length - lines.length
  const off = scan.skills.length - usable.length
  const notes: string[] = []
  if (hidden > 0) notes.push(`另有 ${hidden} 个技能未列出`)
  if (off > 0) notes.push(`${off} 个技能被禁用`)
  return notes.length ? [...lines, `（${notes.join('；')}）`] : lines
}
