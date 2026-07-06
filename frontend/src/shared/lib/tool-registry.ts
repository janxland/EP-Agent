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

  // ── ABC 谱子编辑 ──────────────────────────────────────────────────────────
  abc_to_midi: {
    name: 'abc_to_midi', label: '导出 MIDI', icon: '🎹',
    description: '将 ABC 谱转换为 MIDI 文件', group: 'abc_edit',
  },
  add_ornament: {
    name: 'add_ornament', label: '添加装饰音', icon: '🎶',
    description: '为 ABC 谱指定位置添加装饰音符', group: 'abc_edit',
  },
  analyze_abc: {
    name: 'analyze_abc', label: '分析谱子', icon: '🔍',
    description: '分析 ABC 谱结构、音域、节奏等信息', group: 'abc_edit',
  },
  change_style: {
    name: 'change_style', label: '风格转换', icon: '🎨',
    description: '将 ABC 谱转换为指定音乐风格', group: 'abc_edit',
  },
  change_tempo: {
    name: 'change_tempo', label: '调整速度', icon: '⏱️',
    description: '修改 ABC 谱的 BPM 速度', group: 'abc_edit',
  },
  transpose_abc: {
    name: 'transpose_abc', label: '转调', icon: '🎵',
    description: '将 ABC 谱升高/降低指定半音数', group: 'abc_edit',
  },
  validate_abc: {
    name: 'validate_abc', label: '校验谱子', icon: '✅',
    description: '检查 ABC 谱语法是否合法', group: 'abc_edit',
  },

  // ── 导出工具 ──────────────────────────────────────────────────────────────
  abc_to_sky_json: {
    name: 'abc_to_sky_json', label: '导出 Sky JSON', icon: '📤',
    description: '将 ABC 谱导出为 Sky 游戏格式', group: 'export',
  },
  finish_task: {
    name: 'finish_task', label: '完成任务', icon: '🏁',
    description: '标记当前任务已完成并输出摘要', group: 'system',
  },

  // ── 音频生成 ──────────────────────────────────────────────────────────────
  abc_to_audio_prompt: {
    name: 'abc_to_audio_prompt', label: '提取音乐特征', icon: '🎼',
    description: '从 ABC 谱提取风格/情绪等特征用于音频生成', group: 'audio',
  },
  generate_audio_minimax: {
    name: 'generate_audio_minimax', label: 'MiniMax 生成音频', icon: '🎧',
    description: '调用 MiniMax 接口生成音乐音频', group: 'audio',
  },
  generate_audio_suno: {
    name: 'generate_audio_suno', label: 'Suno 生成音频', icon: '🎵',
    description: '调用 Suno 接口生成音乐音频', group: 'audio',
  },
  generate_cover_minimax: {
    name: 'generate_cover_minimax', label: '生成翻唱', icon: '🎤',
    description: '调用 MiniMax 对指定音频进行翻唱', group: 'audio',
  },
  generate_lyrics_minimax: {
    name: 'generate_lyrics_minimax', label: '生成歌词', icon: '📝',
    description: '调用 MiniMax 生成歌词', group: 'audio',
  },
  get_suno_job_status: {
    name: 'get_suno_job_status', label: '查询 Suno 任务', icon: '🔄',
    description: '轮询 Suno 音频生成任务状态', group: 'audio',
  },
  evolve_audio_prompt: {
    name: 'evolve_audio_prompt', label: '进化音频参数', icon: '🧬',
    description: '基于上次结果迭代优化音频生成参数', group: 'audio',
  },

  // ── H5 海报工具 ───────────────────────────────────────────────────────────
  generate_h5_from_midi: {
    name: 'generate_h5_from_midi', label: '生成 H5 海报', icon: '🖼️',
    description: '将 MIDI/ABC 谱转换为 H5 分享页面', group: 'h5',
  },
  get_h5_template: {
    name: 'get_h5_template', label: '读取 H5 模板', icon: '📄',
    description: '读取指定 H5 模板的 HTML 内容', group: 'h5',
  },
  list_h5_templates: {
    name: 'list_h5_templates', label: '列出 H5 模板', icon: '📋',
    description: '获取所有可用的 H5 海报模板列表', group: 'h5',
  },
  parse_abc_to_json: {
    name: 'parse_abc_to_json', label: '解析 ABC 谱', icon: '🔬',
    description: '将 ABC 谱解析为结构化 JSON 数据', group: 'h5',
  },
  parse_sky_json_to_json: {
    name: 'parse_sky_json_to_json', label: '解析 Sky 谱', icon: '🎮',
    description: '将 Sky 游戏谱子 JSON 解析为标准格式', group: 'h5',
  },
  save_h5_output: {
    name: 'save_h5_output', label: '保存 H5 文件', icon: '💾',
    description: '将生成的 H5 页面保存到工作区', group: 'h5',
  },

  // ── GPT-SoVITS 音色克隆 ───────────────────────────────────────────────────
  sovits_clone_and_save: {
    name: 'sovits_clone_and_save', label: '克隆音色并合成', icon: '🎙️',
    description: '使用参考音频克隆音色并合成语音，自动保存到工作区', group: 'sovits',
  },
  sovits_health_check: {
    name: 'sovits_health_check', label: '检查 SoVITS 服务', icon: '🩺',
    description: '检测 GPT-SoVITS 服务是否在线', group: 'sovits',
  },
  sovits_list_audio_files: {
    name: 'sovits_list_audio_files', label: '列出音频文件', icon: '📂',
    description: '列出工作区内可用的参考音频文件', group: 'sovits',
  },
  sovits_list_models: {
    name: 'sovits_list_models', label: '列出 SoVITS 模型', icon: '🤖',
    description: '获取 GPT-SoVITS 可用模型列表', group: 'sovits',
  },
  sovits_set_model: {
    name: 'sovits_set_model', label: '切换 SoVITS 模型', icon: '🔀',
    description: '切换 GPT-SoVITS 使用的模型', group: 'sovits',
  },
  sovits_tts_and_save: {
    name: 'sovits_tts_and_save', label: 'SoVITS 语音合成', icon: '🔊',
    description: '使用已有音色进行 TTS 语音合成并保存', group: 'sovits',
  },

  // ── MiniMax 音色克隆 ──────────────────────────────────────────────────────
  clone_voice_minimax: {
    name: 'clone_voice_minimax', label: 'MiniMax 克隆音色', icon: '🎤',
    description: '基于上传的音频样本克隆音色', group: 'voice_clone',
  },
  list_cloned_voices: {
    name: 'list_cloned_voices', label: '查看已克隆音色', icon: '🗂️',
    description: '列出所有已克隆的 MiniMax 音色', group: 'voice_clone',
  },
  synthesize_speech_minimax: {
    name: 'synthesize_speech_minimax', label: 'MiniMax 语音合成', icon: '🔊',
    description: '使用克隆音色合成语音', group: 'voice_clone',
  },
  upload_prompt_audio: {
    name: 'upload_prompt_audio', label: '上传增强音频', icon: '⬆️',
    description: '上传增强样本音频（prompt_audio）', group: 'voice_clone',
  },
  upload_voice_sample: {
    name: 'upload_voice_sample', label: '上传音色样本', icon: '📤',
    description: '上传音频样本用于音色克隆', group: 'voice_clone',
  },

  // ── 工作区文件操作 ────────────────────────────────────────────────────────
  copy_workspace_file: {
    name: 'copy_workspace_file', label: '复制文件', icon: '📋',
    description: '在工作区内复制文件到指定路径', group: 'workspace',
  },
  delete_workspace_file: {
    name: 'delete_workspace_file', label: '删除文件', icon: '🗑️',
    description: '删除工作区内指定文件', group: 'workspace',
  },
  edit_workspace_file: {
    name: 'edit_workspace_file', label: '编辑文件', icon: '✏️',
    description: '对工作区文件进行局部编辑（替换/插入/删除行）', group: 'workspace',
  },
  get_workspace_file_url: {
    name: 'get_workspace_file_url', label: '获取文件链接', icon: '🔗',
    description: '获取工作区文件的可访问 URL', group: 'workspace',
  },
  list_workspace_files: {
    name: 'list_workspace_files', label: '列出文件', icon: '📂',
    description: '列出工作区目录下的所有文件', group: 'workspace',
  },
  move_workspace_file: {
    name: 'move_workspace_file', label: '移动/重命名文件', icon: '📁',
    description: '将工作区文件移动到新路径', group: 'workspace',
  },
  read_workspace_file: {
    name: 'read_workspace_file', label: '读取文件', icon: '📖',
    description: '读取工作区内单个文件内容', group: 'workspace',
  },
  read_workspace_files: {
    name: 'read_workspace_files', label: '批量读取文件', icon: '📚',
    description: '批量读取工作区多个文件内容', group: 'workspace',
  },
  rename_workspace_file: {
    name: 'rename_workspace_file', label: '重命名文件', icon: '🏷️',
    description: '重命名工作区内指定文件', group: 'workspace',
  },
  run_write_tasks_in_parallel: {
    name: 'run_write_tasks_in_parallel', label: '并行写入文件', icon: '⚡',
    description: '并发执行多个文件写入任务', group: 'workspace',
  },
  write_workspace_file: {
    name: 'write_workspace_file', label: '写入文件', icon: '💾',
    description: '将内容写入工作区指定文件（新建或覆盖）', group: 'workspace',
  },

  // ── 系统/兼容 ─────────────────────────────────────────────────────────────
  intent_router: {
    name: 'intent_router', label: '意图识别', icon: '🧭',
    description: '分析用户意图，路由到对应处理域', group: 'system',
  },
  voice_clone_router: {
    name: 'voice_clone_router', label: '音色克隆路由', icon: '🎤',
    description: '音色克隆任务调度与执行', group: 'system',
  },
  audio_generator: {
    name: 'audio_generator', label: '音频生成器', icon: '🎧',
    description: '音频生成任务调度与执行', group: 'system',
  },

  // ── 旧版兼容 key ──────────────────────────────────────────────────────────
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
  abc_to_midi_b64: {
    name: 'abc_to_midi_b64', label: '导出 MIDI', icon: '🎹',
    description: '将 ABC 谱导出为 MIDI 文件', group: 'abc_edit',
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
  const n = toolName.toLowerCase()
  const icon =
    n.includes('finish')                                   ? '🏁' :
    n.includes('transpose') || n.includes('key')          ? '🎵' :
    n.includes('tempo')     || n.includes('bpm')          ? '⏱️' :
    n.includes('midi')                                     ? '🎹' :
    n.includes('sky')       || n.includes('convert')      ? '🎮' :
    n.includes('suno')                                     ? '🎵' :
    n.includes('lyrics')                                   ? '📝' :
    n.includes('cover')                                    ? '🎤' :
    n.includes('audio')                                    ? '🎧' :
    n.includes('sovits')    || n.includes('tts')          ? '🔊' :
    n.includes('clone')     || n.includes('voice')        ? '🎤' :
    n.includes('h5')        || n.includes('template')     ? '🖼️' :
    n.includes('router')    || n.includes('intent')       ? '🧭' :
    n.includes('list')      || n.includes('files')        ? '📂' :
    n.includes('read')                                     ? '📖' :
    n.includes('write')     || n.includes('save')         ? '💾' :
    n.includes('delete')    || n.includes('remove')       ? '🗑️' :
    n.includes('rename')    || n.includes('move')         ? '📁' :
    n.includes('copy')                                     ? '📋' :
    n.includes('url')       || n.includes('link')         ? '🔗' :
    n.includes('upload')                                   ? '⬆️' :
    n.includes('analyze')   || n.includes('parse')        ? '🔍' :
    n.includes('validate')  || n.includes('check')        ? '✅' :
    n.includes('edit')      || n.includes('abc')          ? '✏️' :
    n.includes('evolve')    || n.includes('prompt')       ? '🧬' :
    n.includes('parallel')  || n.includes('batch')        ? '⚡' :
    n.includes('model')                                    ? '🤖' :
    n.includes('style')     || n.includes('ornament')     ? '🎨' : '🔧'

  // 工具名转可读中文标签（snake_case → 空格分词，常见词替换）
  const WORD_MAP: Record<string, string> = {
    abc: 'ABC', midi: 'MIDI', sky: 'Sky', h5: 'H5',
    sovits: 'SoVITS', minimax: 'MiniMax', suno: 'Suno',
    to: '→', from: '来自', and: '并', or: '或',
    workspace: '工作区', file: '文件', files: '文件',
    list: '列出', read: '读取', write: '写入', save: '保存',
    delete: '删除', copy: '复制', move: '移动', rename: '重命名',
    edit: '编辑', get: '获取', generate: '生成', parse: '解析',
    analyze: '分析', validate: '校验', upload: '上传',
    clone: '克隆', voice: '音色', audio: '音频', lyrics: '歌词',
    cover: '翻唱', template: '模板', output: '输出', url: '链接',
    finish: '完成', task: '任务', router: '路由', intent: '意图',
    health: '健康检查', check: '检查', status: '状态', job: '任务',
    set: '切换', model: '模型', prompt: '提示词', evolve: '进化',
    parallel: '并行', ornament: '装饰音', style: '风格', tempo: '速度',
    transpose: '转调', sample: '样本', speech: '语音', synthesize: '合成',
  }
  const label = toolName
    .split('_')
    .map((w) => WORD_MAP[w] ?? w)
    .join(' ')

  return {
    name:        toolName,
    label,
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
