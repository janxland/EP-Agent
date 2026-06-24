/**
 * ToolRegistry — 工具注册表客户端
 *
 * 职责：
 *   1. 从后端 /api/health/tools 拉取工具元数据（icon/label/description）
 *   2. 本地默认注册表兜底（后端不可用时使用）
 *   3. useToolRegistry hook — ToolCard 组件调用，O(1) 查找工具元数据
 *
 * 设计原则：
 *   - 前端不再硬编码工具名 → icon/label 映射
 *   - 新增工具只需在后端 @tool 装饰器注册，前端自动发现
 *   - 拉取失败时降级到本地默认注册表，不影响用户体验
 */

import { useEffect, useState } from 'react'

// ── 工具元数据类型 ────────────────────────────────────────────────────────────

export interface ToolMeta {
  name: string         // 工具唯一标识（与后端 function name 一致）
  label: string        // 用户可读名称
  icon: string         // emoji 图标
  description: string  // 功能描述
  group: string        // 所属工具分组
}

// ── 本地默认注册表（兜底，后端拉取失败时使用）─────────────────────────────────

export const DEFAULT_TOOL_REGISTRY: Record<string, ToolMeta> = {
  convert_sky_json: {
    name: 'convert_sky_json', label: '解析 Sky 谱子', icon: '🎮',
    description: '将 Sky 游戏谱子 JSON 转换为 ABC 记谱', group: 'abc_edit',
  },
  abc_transpose: {
    name: 'abc_transpose', label: '转调', icon: '🎵',
    description: '升高/降低指定半音数', group: 'abc_edit',
  },
  abc_set_tempo: {
    name: 'abc_set_tempo', label: '调整速度', icon: '⏱️',
    description: '修改 BPM', group: 'abc_edit',
  },
  abc_to_sky_json: {
    name: 'abc_to_sky_json', label: '导出 Sky JSON', icon: '📤',
    description: '将 ABC 谱导出为 Sky 游戏格式', group: 'abc_edit',
  },
  abc_to_midi_b64: {
    name: 'abc_to_midi_b64', label: '导出 MIDI', icon: '🎹',
    description: '将 ABC 谱导出为 MIDI 文件', group: 'abc_edit',
  },
  intent_router: {
    name: 'intent_router', label: '意图识别', icon: '🧭',
    description: '分析用户意图，路由到对应处理域', group: 'system',
  },
  abc_editor: {
    name: 'abc_editor', label: 'ABC 编辑器', icon: '✏️',
    description: '执行 ABC 谱子编辑操作', group: 'abc_edit',
  },
}

// ── 注册表单例（模块级缓存）──────────────────────────────────────────────────

let _registry: Record<string, ToolMeta> = { ...DEFAULT_TOOL_REGISTRY }
let _fetched = false

/**
 * 从后端 /api/health/tools 拉取工具注册表，合并到本地缓存。
 * 只拉取一次（模块级单例），后续调用直接返回缓存。
 */
export async function fetchToolRegistry(): Promise<Record<string, ToolMeta>> {
  if (_fetched) return _registry
  try {
    const res = await fetch('/api/health/tools', {
      signal: AbortSignal.timeout(3000),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const tools: ToolMeta[] = data.tools ?? []
    // 合并：后端数据覆盖本地默认值
    for (const t of tools) {
      _registry[t.name] = t
    }
    _fetched = true
  } catch {
    // 拉取失败：静默降级到本地默认注册表
    _fetched = true
  }
  return _registry
}

/**
 * 同步查找工具元数据（O(1)）。
 * 若注册表未初始化，返回本地默认值或推断值。
 */
export function getToolMeta(toolName: string): ToolMeta {
  return _registry[toolName] ?? inferToolMeta(toolName)
}

/**
 * 从工具名推断元数据（后端未注册时的最后兜底）。
 */
export function inferToolMeta(toolName: string): ToolMeta {
  const icon =
    toolName.includes('transpose') || toolName.includes('key')  ? '🎵' :
    toolName.includes('tempo')     || toolName.includes('bpm')  ? '⏱️' :
    toolName.includes('midi')                                    ? '🎹' :
    toolName.includes('sky')       || toolName.includes('convert') ? '🎮' :
    toolName.includes('audio')     || toolName.includes('suno') ? '🎧' :
    toolName.includes('voice')     || toolName.includes('sovits') ? '🎤' :
    toolName.includes('router')    || toolName.includes('intent') ? '🧭' :
    toolName.includes('edit')      || toolName.includes('abc')  ? '✏️' : '🔧'

  return {
    name:        toolName,
    label:       toolName.replace(/_/g, ' '),
    icon,
    description: '',
    group:       'unknown',
  }
}

// ── React Hook ────────────────────────────────────────────────────────────────

/**
 * useToolRegistry — ToolCard 组件调用，获取工具元数据。
 *
 * 首次渲染时触发后端拉取（仅一次），后续从缓存取。
 * 拉取期间返回本地默认值，不阻塞渲染。
 *
 * 用法：
 *   const meta = useToolRegistry(toolName)
 *   // meta.icon / meta.label / meta.description
 */
export function useToolRegistry(toolName: string): ToolMeta {
  const [meta, setMeta] = useState<ToolMeta>(() => getToolMeta(toolName))

  useEffect(() => {
    fetchToolRegistry().then((registry) => {
      const m = registry[toolName] ?? inferToolMeta(toolName)
      setMeta(m)
    })
  }, [toolName])

  return meta
}

// ── 意图域注册表（从 /api/health/domains 拉取）────────────────────────────────

export interface DomainMeta {
  name: string
  label: string
  icon: string
  enabled: boolean
}

let _domainRegistry: Record<string, DomainMeta> = {}
let _domainFetched = false

export async function fetchDomainRegistry(): Promise<Record<string, DomainMeta>> {
  if (_domainFetched) return _domainRegistry
  try {
    const res = await fetch('/api/health/domains', {
      signal: AbortSignal.timeout(3000),
    })
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = await res.json()
    const domains: DomainMeta[] = data.domains ?? []
    for (const d of domains) {
      _domainRegistry[d.name] = d
    }
    _domainFetched = true
  } catch {
    _domainFetched = true
  }
  return _domainRegistry
}

export function getDomainMeta(domainName: string): DomainMeta | null {
  return _domainRegistry[domainName] ?? null
}
