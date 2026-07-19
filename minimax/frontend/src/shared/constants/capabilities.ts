import {
  AudioLines,
  FileStack,
  ImageIcon,
  ListChecks,
  MessageSquareText,
  Music2,
  Sparkles,
  Video,
  type LucideIcon,
} from 'lucide-react'
import type { CapabilityId } from '@/state/console-store'

export type CapabilityDefinition = {
  id: CapabilityId
  label: string
  shortLabel: string
  description: string
  icon: LucideIcon
}

export const capabilities: CapabilityDefinition[] = [
  { id: 'text', label: 'Text', shortLabel: 'Text', description: '流式多轮文本生成', icon: MessageSquareText },
  { id: 'image', label: 'Image', shortLabel: 'Image', description: '文本生成图像任务', icon: ImageIcon },
  { id: 'video', label: 'Video', shortLabel: 'Video', description: '文本或图片生成视频', icon: Video },
  { id: 'speech', label: 'Speech', shortLabel: 'Speech', description: '文本转语音', icon: AudioLines },
  { id: 'voice', label: 'Voice Clone', shortLabel: 'Voice', description: '上传、复刻与试听', icon: Sparkles },
  { id: 'music', label: 'Music', shortLabel: 'Music', description: '歌词与风格生成音乐', icon: Music2 },
  { id: 'files', label: 'Files', shortLabel: 'Files', description: '网关文件资产管理', icon: FileStack },
  { id: 'jobs', label: 'Jobs', shortLabel: 'Jobs', description: '异步任务查询与取消', icon: ListChecks },
]

export const capabilityById = Object.fromEntries(capabilities.map((item) => [item.id, item])) as Record<CapabilityId, CapabilityDefinition>
