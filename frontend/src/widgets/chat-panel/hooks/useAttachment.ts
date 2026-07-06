'use client'

/**
 * useAttachment — 附件管理 hook
 *
 * 职责：
 *   - 粘贴文件检测（JSON / MIDI / 音频 / 文本 / 图片）
 *   - 文件上传到工作区
 *   - 上传状态提示（uploading / done / error）
 *   - 附件类型嗅探
 *
 * 与 ChatPanel 解耦：ChatPanel 只需调用 handlePaste / setAttachment，
 * 不关心上传细节。
 */

import { useState, useCallback, useRef, type ClipboardEvent } from 'react'
import {
  readWorkspaceFile,
  uploadFileToWorkspace,
  resolveUploadPath,
  isSkyTxtFile,
} from '@/shared/lib/workspace-files-api'

// ─── 类型 ─────────────────────────────────────────────────────────────────────

export type AttachmentKind = 'json' | 'midi' | 'audio' | 'text' | 'image'

export interface Attachment {
  kind: AttachmentKind
  name: string
  /** 文本内容（text/json/abc）；二进制文件留空 */
  content: string
  /** 工作区相对路径（二进制文件用此字段，不传 base64） */
  workspace_path: string
  size: number
}

export interface UploadTip {
  status: 'uploading' | 'done' | 'error'
  name: string
}

export const KIND_ICON: Record<AttachmentKind, string> = {
  json:  '🎮',
  midi:  '🎹',
  audio: '🎵',
  text:  '📄',
  image: '🖼️',
}

export const KIND_LABEL: Record<AttachmentKind, string> = {
  json:  'Sky JSON',
  midi:  'MIDI',
  audio: '音频',
  text:  '文本',
  image: '图片',
}

/** 根据文件名/内容判断附件类型 */
export function detectKind(name: string, text: string): AttachmentKind {
  const lower = name.toLowerCase()
  if (lower.endsWith('.mid') || lower.endsWith('.midi')) return 'midi'
  if (lower.endsWith('.mp3') || lower.endsWith('.wav') || lower.endsWith('.m4a')) return 'audio'
  if (lower.endsWith('.json')) return 'json'
  if (text.trimStart().startsWith('[') || text.trimStart().startsWith('{')) {
    try {
      const parsed = JSON.parse(text)
      const arr = Array.isArray(parsed) ? parsed : [parsed]
      if (arr[0]?.songNotes) return 'json'
    } catch { /* ignore */ }
    return 'json'
  }
  return 'text'
}

/** 格式化文件大小 */
export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

// ─── Hook ─────────────────────────────────────────────────────────────────────

interface UseAttachmentOptions {
  activeWorkspaceId: string | null
  resolvedProjectId: string
  triggerFileTreeRefresh: () => void
  /** 粘贴纯文本 JSON 后向输入框插入的快捷文字 */
  insertText?: (text: string) => void
  /** 上传完成后向输入框插入 @ 引用 */
  insertMention?: (path: string, label: string, size: number) => void
}

// resolvedProjectId ref — 供 createFromFileRef 闭包读取最新值，避免 stale closure
const _projIdRef = { current: '' }

export function useAttachment({
  activeWorkspaceId,
  resolvedProjectId,
  triggerFileTreeRefresh,
  insertText,
  insertMention,
}: UseAttachmentOptions) {
  // 始终保持最新 resolvedProjectId，供 createFromFileRef 闭包读取
  const projIdRef = useRef(resolvedProjectId)
  projIdRef.current = resolvedProjectId

  const [attachment, setAttachment] = useState<Attachment | null>(null)
  const [uploadTip, setUploadTip] = useState<UploadTip | null>(null)

  // 上传状态自动消除（2.5s）
  const showTip = useCallback((tip: UploadTip) => {
    setUploadTip(tip)
    if (tip.status !== 'uploading') {
      setTimeout(() => setUploadTip(null), 2500)
    }
  }, [])

  const handlePaste = useCallback(async (e: ClipboardEvent) => {
    const dt = e.clipboardData as DataTransfer
    const items = Array.from(dt.items) as DataTransferItem[]

    // 1. 优先处理文件粘贴
    const fileItem = items.find(
      (it) => it.kind === 'file' && (
        it.type.includes('json') ||
        it.type.includes('midi') ||
        it.type.includes('audio') ||
        it.type.includes('text') ||
        it.type === 'application/octet-stream'
      )
    )
    if (fileItem) {
      e.preventDefault()
      const file = fileItem.getAsFile()
      if (!file) return

      const isAudio = file.type.includes('audio') || /\.(mp3|wav|m4a|ogg|flac)$/i.test(file.name)
      const isMidi  = file.type.includes('midi')  || /\.(mid|midi)$/i.test(file.name)

      if (!activeWorkspaceId) {
        showTip({ status: 'error', name: file.name })
        return
      }

      const kind: AttachmentKind = isAudio ? 'audio' : isMidi ? 'midi' : 'text'
      const isSkyTxt = file.name.toLowerCase().endsWith('.txt') ? await isSkyTxtFile(file) : false
      const destPath = resolveUploadPath(file, isSkyTxt)

      showTip({ status: 'uploading', name: file.name })
      try {
        await uploadFileToWorkspace(activeWorkspaceId, file, destPath, resolvedProjectId)
        const content = (!isAudio && !isMidi) ? await file.text() : ''
        setAttachment({ kind, name: file.name, content, workspace_path: destPath, size: file.size })
        showTip({ status: 'done', name: file.name })
        insertMention?.(destPath, file.name, file.size)
        triggerFileTreeRefresh()
      } catch (err) {
        console.error('[粘贴上传失败]', file.name, err)
        showTip({ status: 'error', name: file.name })
      }
      return
    }

    // 2. 纯文本粘贴：检测是否像 Sky JSON
    const textItem = items.find((it) => it.kind === 'string' && it.type === 'text/plain')
    if (textItem) {
      textItem.getAsString((text: string) => {
        const trimmed = text.trim()
        if (trimmed.length > 200 && (trimmed.startsWith('[') || trimmed.startsWith('{'))) {
          e.preventDefault()
          const kind = detectKind('paste.json', trimmed)
          setAttachment({ kind, name: 'paste.json', content: trimmed, size: trimmed.length, workspace_path: '' })
          insertText?.('帮我加载这首谱子')
        }
      })
    }
  }, [activeWorkspaceId, resolvedProjectId, triggerFileTreeRefresh, insertText, insertMention, showTip])

  /** 从工作区文件引用（@ 选择）创建附件 */
  const createFromFileRef = useCallback(async (
    path: string,
    label: string,
    size: number,
  ): Promise<Attachment | null> => {
    if (!activeWorkspaceId) return null
    // 通过 ref 读取最新 resolvedProjectId，避免闭包捕获旧值导致跨项目读取错误文件
    const currentProjId = projIdRef.current
    const ext = label.split('.').pop()?.toLowerCase() ?? ''
    const isMidi  = ['mid', 'midi'].includes(ext)
    const isAudio = ['mp3', 'wav', 'm4a', 'ogg', 'flac'].includes(ext)
    const isImage = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(ext)
    const isText  = ['abc', 'txt', 'md', 'json', 'html', 'csv'].includes(ext)

    try {
      if (isMidi || isAudio || isImage) {
        const kind: AttachmentKind = isMidi ? 'midi' : isAudio ? 'audio' : 'image'
        return { kind, name: label, content: '', workspace_path: path, size }
      } else if (isText) {
        const content = await readWorkspaceFile(activeWorkspaceId, path, currentProjId)
        const kind: AttachmentKind = ext === 'json' ? 'json' : 'text'
        return { kind, name: label, content, workspace_path: path, size: content.length }
      }
    } catch { /* 静默失败 */ }
    return null
  }, [activeWorkspaceId]) // projIdRef 通过 ref 读取，不需要加入依赖

  const clearAttachment = useCallback(() => setAttachment(null), [])

  return {
    attachment,
    setAttachment,
    clearAttachment,
    uploadTip,
    handlePaste,
    createFromFileRef,
  }
}
