/**
 * 场景（Scenes）—— 按会话生效的提示词预设。
 *
 * 一个场景 = 名称 + 关键词 + 附加指令 + 输出规范 + 所需技能。消息进来时按三档挑一个：
 * **绑定 → 关键词 → 上次用过的**，都没有就不加场景段（默认行为一字不变）。
 *
 * 为什么单独一个文件、不塞进配置键、也不塞进记忆文件：
 * - 配置键是字符串，多字段结构化内容塞进去就等于让用户手写 JSON；
 * - 记忆文件会被 `compressIfNeeded`/`extractFactsIfNeeded` 用 LLM 改写，还有 `quarantine()`
 *   把整个文件搬走的路径 —— **用户手写的场景不该走那条路**（被模型改写或被隔离都不像话）。
 *
 * 存取纪律照抄记忆文件：版本号 + 未知版本**留档不猜** + `writeFileAtomic` + 只回写脏数据 +
 * `loadIssue`/`saveIssue` 不抛但必记。
 *
 * **场景不给模型碰**：没有任何模型可调的工具能改场景。配置类的东西只能用户显式改
 * （同 `隐私` 那条注释的立场）。调用方包括 CLI、单轮面板覆盖与内置会话指令。
 */
import { existsSync, mkdirSync, readFileSync, renameSync } from 'fs'
import { homedir } from 'os'
import { join } from 'path'
import { writeFileAtomic } from '../utils/atomicWrite.js'
import { frameLocalData } from './assistantMemory.js'
import { renderSkillRefs, referencedSkillIds, describeSkill } from './assistantSkills.js'

export const SCENES_FORMAT_VERSION = 1
const SCENES_FILE = join(homedir(), '.weflow-cli', 'assistant_scenes.json')

/** 保留字：`场景 无` 用来解绑，所以不能拿它当 id */
export const SCENE_RESERVED_IDS = ['无', 'none', 'off']

export interface Scene {
  id: string
  name: string
  /** 消息里出现任一个就命中（不区分大小写） */
  keywords: string[]
  /** 附加指令，可以写 `{{skill:<id>}}` */
  instruction: string
  /** 输出规范（可选） */
  outputSpec: string
  /** 需要的技能 id —— 与指令里的行内引用取并集 */
  skills: string[]
  enabled: boolean
}

export type MatchHow = 'explicit' | 'binding' | 'keyword' | 'lastUsed'
export interface SceneMatch { scene: Scene; how: MatchHow }

interface ScenesFile {
  version: number
  scenes: Scene[]
  /** 会话（userId）→ 场景 id。微信那条路上 userId 就是 conversationId */
  bindings: Record<string, string>
  /** 每个会话上一次实际用了哪个场景（没绑定时的兜底） */
  lastUsed: Record<string, string>
}

function normalizeScene(raw: any): Scene | null {
  if (!raw || typeof raw !== 'object') return null
  const id = String(raw.id || '').trim()
  if (!id) return null
  const asList = (v: any): string[] => Array.isArray(v)
    ? v.map((x: any) => String(x || '').trim()).filter(Boolean)
    : []
  return {
    id,
    name: String(raw.name || id).trim() || id,
    keywords: asList(raw.keywords),
    instruction: typeof raw.instruction === 'string' ? raw.instruction : '',
    outputSpec: typeof raw.outputSpec === 'string' ? raw.outputSpec : '',
    skills: asList(raw.skills),
    // 缺省是"开"。只有显式 false 才算关（老数据没有这个字段）
    enabled: raw.enabled === undefined ? true : raw.enabled !== false,
  }
}

/**
 * id 规则：**允许中文**（这是给中文用户用的，`场景 日报` 比 `场景 daily-report` 自然得多），
 * 但禁掉空白、路径分隔符、以及引号和尖括号。
 *
 * 引号尖括号不是洁癖：id 会出现在 `frameLocalData('scene.<id>', …)` 的
 * `source="…"` 属性里（见 `assistantMemory.frameLocalData`，它只转义**正文**里的框标签，
 * 不转义标签），带 `"` 的 id 能把属性闭合掉。这里挡住，`safeSceneLabel` 再兜一层。
 */
const SCENE_ID_BANNED = /[\s/\\:*?"'<>|]/

export function validateSceneId(id: string): string | null {
  const v = String(id || '').trim()
  if (!v) return '场景 id 不能为空'
  if (v.length > 40) return '场景 id 太长（40 字以内）'
  if (SCENE_ID_BANNED.test(v)) return '场景 id 不能含空白、路径分隔符、引号或尖括号'
  if (v.startsWith('.')) return '场景 id 不能以点开头'
  if (SCENE_RESERVED_IDS.includes(v.toLowerCase())) return `「${v}」是保留字（用来解绑），换一个 id`
  return null
}

/** 把 id 放进提示词标签前再洗一遍（纵深防御：校验是一道，这里是第二道） */
function safeSceneLabel(id: string): string {
  return String(id || '').replace(/[^0-9A-Za-z\u0080-￿._-]/g, '_').slice(0, 40)
}

export class AssistantScenes {
  private scenes: Scene[] = []
  private bindings: Record<string, string> = {}
  private lastUsed: Record<string, string> = {}
  private dirty = false
  private loadIssue = ''
  private saveIssue: string | null = null

  constructor() { this.load() }

  get problem(): string { return this.loadIssue }
  get lastSaveError(): string | null { return this.saveIssue }

  /** 把读不出来的文件改名留档——用户手写的场景，**不许静默丢弃** */
  private quarantine(reason: string): void {
    const target = `${SCENES_FILE}.unreadable-${new Date().toISOString().replace(/[:.]/g, '-')}`
    try {
      renameSync(SCENES_FILE, target)
      this.loadIssue = `${reason}；原文件已留档为 ${target.split(/[\\/]/).pop()}`
    } catch (error: any) {
      this.loadIssue = `${reason}；留档也失败了（${error?.message ?? error}），未改动原文件`
    }
    this.scenes = []
    this.bindings = {}
    this.lastUsed = {}
  }

  private load(): void {
    if (!existsSync(SCENES_FILE)) return
    let raw: any
    try {
      raw = JSON.parse(readFileSync(SCENES_FILE, 'utf8'))
    } catch (error: any) {
      this.quarantine(`场景文件无法解析（${error?.message?.slice(0, 80) ?? error}）`)
      return
    }
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
      this.quarantine('场景文件不是一个对象')
      return
    }
    if (raw.version !== SCENES_FORMAT_VERSION) {
      // 不猜、不迁移（记忆文件同款纪律）
      this.quarantine(`场景文件版本是 ${JSON.stringify(raw.version)}，本程序只认 ${SCENES_FORMAT_VERSION}`)
      return
    }
    const list = Array.isArray(raw.scenes) ? raw.scenes : []
    const seen = new Set<string>()
    for (const item of list) {
      const scene = normalizeScene(item)
      if (!scene) continue
      if (seen.has(scene.id)) continue   // 文件被手改出重复 id：留第一个，不崩
      seen.add(scene.id)
      this.scenes.push(scene)
    }
    const asMap = (v: any): Record<string, string> => {
      if (!v || typeof v !== 'object' || Array.isArray(v)) return {}
      const out: Record<string, string> = {}
      for (const [k, val] of Object.entries<any>(v)) {
        const s = String(val || '').trim()
        if (s) out[k] = s
      }
      return out
    }
    this.bindings = asMap(raw.bindings)
    this.lastUsed = asMap(raw.lastUsed)
  }

  save(): void {
    try {
      const dir = join(homedir(), '.weflow-cli')
      if (!existsSync(dir)) mkdirSync(dir, { recursive: true })
      const payload: ScenesFile = {
        version: SCENES_FORMAT_VERSION,
        scenes: this.scenes,
        bindings: this.bindings,
        lastUsed: this.lastUsed,
      }
      writeFileAtomic(SCENES_FILE, JSON.stringify(payload, null, 1))
      this.dirty = false
      this.saveIssue = null
    } catch (error: any) {
      // 不抛：磁盘打嗝不该让一次 CLI 操作炸掉。但也不吭声就不对了 —— 用户以为自己加上了。
      this.saveIssue = String(error?.message ?? error).slice(0, 120)
    }
  }

  // ---------- 读 ----------

  list(): Scene[] { return [...this.scenes] }
  get(id: string): Scene | undefined { return this.scenes.find(s => s.id === id) }
  binding(userId: string): string { return this.bindings[userId] || '' }
  lastUsedFor(userId: string): string { return this.lastUsed[userId] || '' }

  // ---------- 写（都只改内存，由调用方决定何时 save） ----------

  add(scene: Scene): { ok: boolean; reason?: string } {
    const idError = validateSceneId(scene.id)
    if (idError) return { ok: false, reason: idError }
    if (this.get(scene.id)) return { ok: false, reason: `场景「${scene.id}」已经存在（要改就先 remove）` }
    this.scenes.push({ ...scene, name: scene.name || scene.id, enabled: scene.enabled !== false })
    this.dirty = true
    return { ok: true }
  }

  remove(id: string): boolean {
    const before = this.scenes.length
    this.scenes = this.scenes.filter(s => s.id !== id)
    if (this.scenes.length === before) return false
    // 顺手清理指向它的绑定/上次记录，免得留下一堆悬空 id
    for (const [uid, sid] of Object.entries(this.bindings)) if (sid === id) delete this.bindings[uid]
    for (const [uid, sid] of Object.entries(this.lastUsed)) if (sid === id) delete this.lastUsed[uid]
    this.dirty = true
    return true
  }

  setEnabled(id: string, on: boolean): boolean {
    const scene = this.get(id)
    if (!scene) return false
    scene.enabled = on
    this.dirty = true
    return true
  }

  bind(userId: string, sceneId: string | null): { ok: boolean; reason?: string } {
    if (sceneId === null) {
      delete this.bindings[userId]
      this.dirty = true
      return { ok: true }
    }
    const scene = this.get(sceneId)
    if (!scene) return { ok: false, reason: `没有场景「${sceneId}」` }
    if (!scene.enabled) return { ok: false, reason: `场景「${sceneId}」已被停用，先启用再绑` }
    this.bindings[userId] = sceneId
    this.dirty = true
    return { ok: true }
  }

  noteUsed(userId: string, sceneId: string): void {
    if (this.lastUsed[userId] === sceneId) return
    this.lastUsed[userId] = sceneId
    this.dirty = true
  }

  // ---------- 三档匹配 ----------

  /** 只允许显式覆盖一个当前已启用的场景；它不改变会话绑定或最近使用记录。 */
  resolveExplicit(sceneId: string): { match: SceneMatch | null; why?: string } {
    const scene = this.get(sceneId)
    if (!scene) return { match: null, why: `没有场景「${sceneId}」` }
    if (!scene.enabled) return { match: null, why: `场景「${sceneId}」已停用` }
    return { match: { scene, how: 'explicit' } }
  }

  /**
   * 挑一个场景。**只认唯一**：多个场景命中同样长的关键词时**拒绝**（返回 null + 理由），
   * 不猜 —— 与本仓 `resolveUniqueTalker`、`resolvePanelUserId` 是同一条纪律。
   */
  resolve(userId: string, text: string, opts: { remember?: boolean } = {}): { match: SceneMatch | null; why?: string } {
    const enabled = this.scenes.filter(s => s.enabled)
    if (!enabled.length) return { match: null }

    const boundId = this.bindings[userId]
    if (boundId) {
      const scene = enabled.find(s => s.id === boundId)
      if (scene) return this.land(userId, { scene, how: 'binding' }, opts)
    }

    const hay = String(text || '').toLowerCase()
    if (hay) {
      // 每个场景取它**最长**的那个命中关键词；比长度，不比关键词个数
      const hits: Array<{ scene: Scene; len: number; keyword: string }> = []
      for (const scene of enabled) {
        let best: { len: number; keyword: string } | null = null
        for (const kw of scene.keywords) {
          const k = kw.toLowerCase()
          if (!k || !hay.includes(k)) continue
          if (!best || k.length > best.len) best = { len: k.length, keyword: kw }
        }
        if (best) hits.push({ scene, len: best.len, keyword: best.keyword })
      }
      if (hits.length) {
        const top = Math.max(...hits.map(h => h.len))
        const winners = hits.filter(h => h.len === top)
        if (winners.length === 1) {
          return this.land(userId, { scene: winners[0].scene, how: 'keyword' }, opts)
        }
        const names = winners.map(w => `「${w.scene.name}」(关键词 ${w.keyword})`).join('、')
        return { match: null, why: `有 ${winners.length} 个场景同样匹配：${names} —— 这次不带场景（避免猜错），可用「场景 <id>」固定一个` }
      }
    }

    const lastId = this.lastUsed[userId]
    if (lastId) {
      const scene = enabled.find(s => s.id === lastId)
      if (scene) return this.land(userId, { scene, how: 'lastUsed' }, opts)
    }
    return { match: null }
  }

  private land(userId: string, match: SceneMatch, opts: { remember?: boolean }): { match: SceneMatch } {
    if (opts.remember !== false && this.lastUsed[userId] !== match.scene.id) {
      this.lastUsed[userId] = match.scene.id
      this.dirty = true
    }
    return { match }
  }
}

/**
 * 渲染一个场景成提示词里的一段。整段用 `frameLocalData` 包住 ——
 * 指令和技能说明都是**本地磁盘上的内容**，可能含 `</weflow-local-data>` 想越狱。
 */
export function renderScene(scene: Scene): string {
  const parts: string[] = []
  const instruction = renderSkillRefs(scene.instruction)
  if (instruction.trim()) parts.push(instruction.trim())
  if (scene.outputSpec.trim()) parts.push(`输出规范：\n${scene.outputSpec.trim()}`)
  // 显式声明的技能（行内 {{skill:}} 已经在 instruction 里就地渲染过了，这里只补没提到的那些）
  const declared = referencedSkillIds('', scene.skills)
  const inline = referencedSkillIds(scene.instruction, [])
  const rest = declared.filter(id => !inline.includes(id))
  if (rest.length) {
    parts.push(['技能要求：', ...rest.map(id => `· ${describeSkill(id).line}`)].join('\n'))
  }
  return frameLocalData(`scene.${safeSceneLabel(scene.id)}`, parts.join('\n\n'))
}

export interface SceneCommand {
  kind: 'list' | 'bind' | 'unbind'
  id?: string
}

/**
 * 解析 `场景` 指令。**只有后半段真的是已存在的场景 id 才算指令**，
 * 否则返回 null 交给模型 —— 不然「场景切换怎么用」这种正常问话会被当成命令吃掉。
 * （现有内置指令都是全等匹配，这里是唯一一条带参数的，所以这条边界要写死。）
 */
export function parseSceneCommand(text: string, scenes: AssistantScenes): SceneCommand | null {
  const t = String(text || '').trim()
  if (t === '场景') return { kind: 'list' }
  const m = /^场景[\s:=]+(.+)$/.exec(t)
  if (!m) return null
  const arg = m[1].trim().replace(/^[「"']|[」"']$/g, '')
  if (SCENE_RESERVED_IDS.includes(arg.toLowerCase())) return { kind: 'unbind' }
  if (!scenes.get(arg)) return null   // 不是真实场景 id → 不当指令
  return { kind: 'bind', id: arg }
}

/** 全局单例：CLI 与助手共用一份（同 `configService`/`assistantMemory` 的做法） */
export const assistantScenes = new AssistantScenes()
