/**
 * 全局共享常量
 * 集中管理，消除各组件中的魔法数字
 */

// ── 布局尺寸 ──────────────────────────────────────────────────────────────────

/** 专业模式：右侧对话面板宽度约束 */
export const CHAT_PANEL = {
  MIN_WIDTH:     280,
  MAX_WIDTH:     640,
  DEFAULT_WIDTH: 380,
} as const

/** 侧边栏宽度 */
export const SIDEBAR_WIDTH = 192   // 48 * 4 = w-48

// ── Session ───────────────────────────────────────────────────────────────────

/** Session localStorage key */
export const STORAGE_KEY_ACTIVE_SESSION = 'ep_agent_active_session_id'

// ── API ───────────────────────────────────────────────────────────────────────

/** 后端健康检查轮询间隔（毫秒） */
export const HEALTH_CHECK_INTERVAL_MS = 30_000

/** SSE 连接超时（毫秒） */
export const SSE_TIMEOUT_MS = 30_000

/** 工具注册表拉取超时（毫秒） */
export const TOOL_REGISTRY_TIMEOUT_MS = 3_000

// ── 音频 ──────────────────────────────────────────────────────────────────────

/** 音色克隆：支持的音频格式（前端 file input accept 属性） */
export const VOICE_CLONE_ACCEPT = 'audio/mpeg,audio/mp4,audio/wav,.mp3,.m4a,.wav'

/** 音色克隆：最大文件大小（字节） */
export const VOICE_CLONE_MAX_BYTES = 20 * 1024 * 1024   // 20MB

/** 音频生成：服务商列表 */
export const AUDIO_PROVIDERS = ['auto', 'minimax', 'suno'] as const
export type AudioProviderOption = typeof AUDIO_PROVIDERS[number]

// ── 乐谱编辑器 ────────────────────────────────────────────────────────────────

/** ABC 渲染：最小谱面宽度（像素） */
export const ABC_STAFF_MIN_WIDTH = 300

/** ABC 渲染：缩放比例 */
export const ABC_RENDER_SCALE = 1.1
