/**
 * domain.ts — 意图域配置
 *
 * 后端新增域时只需在此处添加一条记录，无需修改任何组件。
 * 角色 ID 须与后端 role_config.py 的 ROLE_CONFIG key 保持一致。
 */

export interface DomainMeta {
  icon: string
  label: string
}

export const DOMAIN_CONFIG: Record<string, DomainMeta> = {
  convert:        { icon: '🎮', label: '解析谱子' },
  edit:           { icon: '✏️', label: '编辑谱子' },
  create:         { icon: '🎵', label: '创作谱子' },
  audio:          { icon: '🎧', label: '生成音频' },
  voice:          { icon: '🎤', label: 'MiniMax音色' },
  sovits:         { icon: '🎙️', label: '音色克隆' },
  query:          { icon: '🔍', label: '查询分析' },
  'convert+edit': { icon: '🎮', label: '解析并编辑' },
  h5_create:      { icon: '🎨', label: 'H5 页面' },
  h5_edit:        { icon: '🖌️', label: 'H5 编辑' },
}

/** 根据域 ID 获取配置，未知域返回 null */
export function getDomainMeta(domain: string | null | undefined): DomainMeta | null {
  if (!domain) return null
  return DOMAIN_CONFIG[domain] ?? null
}

/**
 * 快捷提示词：按角色 ID 返回对应的快捷指令
 * ⚠️ 角色 ID 须与后端 role_config.py 的 ROLE_CONFIG key 严格一致：
 *   abc_expert / music_producer / voice_cloner / h5_designer
 */
export const ROLE_HINTS: Record<string, string[]> = {
  abc_expert: [
    '升高一个八度',
    '加快节奏',
    '这首是什么调？',
    '转成 C 大调',
    '帮我简化编排',
  ],
  // voice_cloner 对应后端 role_config.py 中的 "voice_cloner"
  voice_cloner: [
    '克隆我的声音',
    '上传参考音频',
    '用克隆音色朗读文本',
    '查看音色库',
    '导出克隆音频',
  ],
  music_producer: [
    '生成中国风配乐',
    '生成电子舞曲',
    '生成轻音乐背景',
    '调整 BPM',
    '添加鼓点',
  ],
  h5_designer: [
    '上传 MIDI 生成播放页',
    '生成苹果风格海报',
    '换一个模板',
    '修改海报标题',
    '生成分享链接',
  ],
  default: [
    '升高一个八度',
    '加快节奏',
    '生成中国风配乐',
    '克隆我的声音',
    '这首是什么调？',
  ],
}

/** 根据角色 ID 获取快捷提示词列表 */
export function getRoleHints(roleId: string): string[] {
  return ROLE_HINTS[roleId] ?? ROLE_HINTS.default
}
