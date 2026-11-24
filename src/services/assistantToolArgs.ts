/**
 * 工具参数的**宽松强转**：抹平同一个值的不同 JSON 写法，**不负责"帮模型改对"**。
 *
 * ## 为什么要这一层
 *
 * 模型给参数时的类型错误，在我们这里有几种不同命运：
 *
 * - **字符串数字**（`limit: "20"`）：本来就有 `Number()` 兜着，不是问题。
 * - **字符串布尔**（`confirm: "true"`、`with_text: "true"`）：`args.confirm !== true`
 *   会判成"没确认"，`args.with_text === true` 会判成 false。**两处都是静默降级、不报错**——
 *   模型以为用户看到正文了，用户实际只拿到片段。这是最坏的一种失败：错了却没有任何提示。
 * - **单值没包数组**：模型只给一个时忘了包 `[]`。
 * - **可选属性给了 `null`**：语义上等于"没给"，但下游 `String(null)` 会变成 `"null"`。
 *
 * pi 是这么做的（`extensions.md:174`）：
 * > Before validation, loosely typed arguments are coerced: numeric strings to numbers,
 * > `"true"` to `true`, a single value to a one-item array, `null` in an optional property dropped.
 *
 * ## 三条纪律
 *
 * 1. **只在 schema 说是那个类型时才转**。`limit` 转了数字、`keyword` 绝不能转——
 *    转错类型比不转更糟，因为它会让"参数错"变成"行为诡异"。
 * 2. **转不动就原样留着**：`limit: "abc"` 不转，让后面的校验去报
 *    `(参数错误: limit 需要是数字)`——那比"我猜你是想要 0"清楚得多。
 * 3. **不认识的工具原样返回**：没有 schema 就没有依据，宁可不动。
 *
 * 与 pi 的差别：pi 转完还跑一遍 JSON Schema 严格校验；我们的校验散在 31 个 `case` 里
 * （而且错误文案都带业务语义，如"since/until 可以写 3d / 2w / 12h"），
 * 所以这一层只做**类型抹平**，校验仍旧交给各工具自己——见 D-178。
 */
import { TOOL_DEFS } from './assistantTools.js'

type ArgType = 'boolean' | 'number' | 'array' | 'other'

/** 工具名 → 参数名 → schema 声明的类型。启动时算一次，之后只查表 */
let schemaIndex: Map<string, Map<string, { type: ArgType; optional: boolean }>> | null = null

function indexSchema(): Map<string, Map<string, { type: ArgType; optional: boolean }>> {
  if (schemaIndex) return schemaIndex
  const index = new Map<string, Map<string, { type: ArgType; optional: boolean }>>()
  for (const def of TOOL_DEFS) {
    const fn = (def as any).function
    const name = String(fn?.name ?? '')
    if (!name) continue
    const params = fn?.parameters ?? {}
    const props = params.properties ?? {}
    const required: string[] = Array.isArray(params.required) ? params.required : []
    const fields = new Map<string, { type: ArgType; optional: boolean }>()
    for (const [key, raw] of Object.entries(props as Record<string, any>)) {
      const declared = String(raw?.type ?? '')
      const type: ArgType = declared === 'boolean' ? 'boolean'
        : declared === 'number' || declared === 'integer' ? 'number'
        : declared === 'array' ? 'array'
        : 'other'
      fields.set(key, { type, optional: !required.includes(key) })
    }
    index.set(name, fields)
  }
  schemaIndex = index
  return index
}

/** 只认模型真实可能给出的写法，别的一律不动（`'yes'`/`'on'` 这类不要自作聪明） */
function coerceBoolean(value: unknown): boolean | undefined {
  if (typeof value === 'boolean') return value
  if (typeof value === 'string') {
    const t = value.trim().toLowerCase()
    if (t === 'true') return true
    if (t === 'false') return false
    return undefined
  }
  if (typeof value === 'number') {
    if (value === 1) return true
    if (value === 0) return false
  }
  return undefined
}

function coerceNumber(value: unknown): number | undefined {
  if (typeof value === 'number') return Number.isFinite(value) ? value : undefined
  if (typeof value === 'string') {
    const t = value.trim()
    // 只接受"整体就是一个数"的字符串：`'20'` 转，`'20 条'`/`'abc'`/`''` 不转
    if (/^-?\d+(\.\d+)?$/.test(t)) {
      const n = Number(t)
      return Number.isFinite(n) ? n : undefined
    }
  }
  return undefined
}

/**
 * 把模型给的参数按 schema 抹平类型。**纯函数**：不改入参，返回新对象。
 *
 * 不认识的工具、没有 schema 的工具、schema 里没声明的字段，一律原样保留——
 * 这一层不该成为新的失败来源。
 */
export function coerceToolArgs(toolName: string, args: Record<string, any>): Record<string, any> {
  const fields = indexSchema().get(toolName)
  if (!fields) return args

  const out: Record<string, any> = {}
  for (const [key, raw] of Object.entries(args ?? {})) {
    const field = fields.get(key)
    // 没在 schema 里声明的字段：原样带过去，让工具自己去判断（很多工具有可选透传字段）
    if (!field) { out[key] = raw; continue }

    // ① 可选属性上的 null 丢掉 —— 语义就是"没给"
    if (raw === null && field.optional) continue
    if (raw === undefined) continue

    // ② 按声明的类型转
    if (field.type === 'boolean') {
      const v = coerceBoolean(raw)
      out[key] = v === undefined ? raw : v
      continue
    }
    if (field.type === 'number') {
      const v = coerceNumber(raw)
      out[key] = v === undefined ? raw : v
      continue
    }
    if (field.type === 'array') {
      out[key] = Array.isArray(raw) ? raw : [raw]
      continue
    }
    out[key] = raw
  }
  return out
}

/** 测试用：清掉 schema 缓存（改过 TOOL_DEFS 之后要重算） */
export function resetToolArgsIndexForTest(): void {
  schemaIndex = null
}
