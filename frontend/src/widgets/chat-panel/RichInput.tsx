'use client'

/**
 * RichInput — Tiptap 富文本输入框
 *
 * 修复要点：
 * 1. doSend 用 ref 存储，解决 handleKeyDown 闭包陷阱
 * 2. 文件列表只加载一次，去掉重复 effect
 * 3. MentionNodeView hideTimer 在卸载时清理
 * 4. tooltip 先计算位置再显示，消除 (0,0) 闪烁
 * 5. MentionList 键盘监听改为 capture 模式，避免与 Tiptap Enter 冲突
 * 6. 所有 import 在顶部
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode, CSSProperties, RefObject, MutableRefObject } from 'react'
import {
  useEditor, EditorContent,
  NodeViewWrapper, ReactNodeViewRenderer,
  ReactRenderer,
  type Editor, type NodeViewProps,
} from '@tiptap/react'
import StarterKit from '@tiptap/starter-kit'
import Mention from '@tiptap/extension-mention'
import { mergeAttributes } from '@tiptap/core'
import tippy, { type Instance as TippyInstance } from 'tippy.js'
import 'tippy.js/dist/tippy.css'

import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import {
  listWorkspaceFiles,
  uploadFileToWorkspace,
  writeWorkspaceFile,
  fileToBase64,
  getFileIcon,
  fmtFileSize,
  type WorkspaceFile,
} from '@/shared/lib/workspace-files-api'
import { FILE_REF_EVENT } from '@/widgets/workspace-sidebar/WorkspaceFileTree'

// ─── 文件类型工具（集中定义，两个文件共用逻辑保持一致）────────────────────────

export const FILE_IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'])
export const FILE_AUDIO_EXTS = new Set(['mid', 'midi', 'mp3', 'wav', 'm4a', 'ogg', 'flac'])

export function getFileExt(name: string) {
  return name.split('.').pop()?.toLowerCase() ?? ''
}

export function getFileTypeLabel(ext: string): string {
  if (ext === 'mid' || ext === 'midi') return 'MIDI 文件'
  if (FILE_AUDIO_EXTS.has(ext)) return '音频文件'
  if (FILE_IMAGE_EXTS.has(ext)) return '图片'
  if (ext === 'json') return 'Sky JSON'
  if (ext === 'abc') return 'ABC 谱'
  if (ext === 'html' || ext === 'htm') return 'H5 页面'
  if (ext === 'txt' || ext === 'md') return '文本文件'
  return '文件'
}

export function getChipColorClass(ext: string): string {
  if (FILE_AUDIO_EXTS.has(ext))
    return 'bg-violet-100 text-violet-700 border-violet-200 hover:bg-violet-200'
  if (FILE_IMAGE_EXTS.has(ext))
    return 'bg-sky-100 text-sky-700 border-sky-200 hover:bg-sky-200'
  if (ext === 'json')
    return 'bg-emerald-100 text-emerald-700 border-emerald-200 hover:bg-emerald-200'
  return 'bg-orange-100 text-orange-700 border-orange-200 hover:bg-orange-200'
}

// ─── FileTooltip — 悬浮信息卡片（Portal 到 body）────────────────────────────

interface FileTooltipProps {
  name: string
  ext: string
  size: number
  path: string
  workspaceId?: string
  anchorRef: RefObject<HTMLElement>
  onMouseEnter: () => void
  onMouseLeave: () => void
}

export function FileTooltip({
  name, ext, size, path, workspaceId,
  anchorRef, onMouseEnter, onMouseLeave,
}: FileTooltipProps) {
  const [style, setStyle] = useState<CSSProperties>({ visibility: 'hidden', position: 'fixed' })
  // 走 FastAPI StaticFiles 静态直链，绕开 API 路由缓冲，<img src> 可直接渲染
  const imgSrc = FILE_IMAGE_EXTS.has(ext) && workspaceId
    ? `/workspace/${workspaceId}/${path}`
    : null

  // 计算位置后再显示，避免闪烁到 (0,0)
  useEffect(() => {
    if (!anchorRef.current) return
    const rect = anchorRef.current.getBoundingClientRect()
    setStyle({
      position: 'fixed',
      bottom: window.innerHeight - rect.top + 8,
      left: Math.max(8, Math.min(rect.left, window.innerWidth - 220)),
      zIndex: 9999,
      visibility: 'visible',
    })
  }, [anchorRef])

  return createPortal(
    <div
      style={style}
      onMouseEnter={onMouseEnter}
      onMouseLeave={onMouseLeave}
      className="w-52 bg-white rounded-xl shadow-xl border border-gray-100 overflow-hidden pointer-events-auto"
    >
      {imgSrc && (
        <div className="w-full h-28 bg-gray-50 overflow-hidden">
          <img
            src={imgSrc}
            alt={name}
            className="w-full h-full object-cover"
            onError={(e) => {
              const el = e.currentTarget.parentElement
              if (el) el.style.display = 'none'
            }}
          />
        </div>
      )}
      <div className="px-3 py-2.5">
        <div className="flex items-start gap-2">
          <span className="text-lg shrink-0 mt-0.5">{getFileIcon(ext)}</span>
          <div className="flex-1 min-w-0">
            <p className="text-xs font-semibold text-gray-800 break-all leading-snug">{name}</p>
            <p className="mt-0.5 text-[10px] text-gray-400">{getFileTypeLabel(ext)}</p>
          </div>
        </div>
        <div className="mt-2 pt-2 border-t border-gray-50 flex items-center justify-between gap-2">
          <span className="text-[10px] text-gray-400 font-mono shrink-0">{fmtFileSize(size)}</span>
          <span className="text-[10px] text-gray-300 font-mono truncate" title={path}>{path}</span>
        </div>
      </div>
    </div>,
    document.body
  )
}

// ─── FileChip — 可复用的文件引用 chip（输入框 & 消息气泡共用）─────────────────

interface FileChipProps {
  label: string   // 文件名
  path: string    // 文件路径（用于图片预览 URL）
  size?: number   // 文件大小（字节），可选
  workspaceId?: string
  /** 是否在橙色气泡内，若是则用白色半透明胶囊样式 */
  inOrangeBubble?: boolean
}

export function FileChip({ label, path, size = 0, workspaceId, inOrangeBubble = false }: FileChipProps) {
  const ext = getFileExt(label)
  const [hovered, setHovered] = useState(false)
  const chipRef = useRef<HTMLSpanElement>(null)
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null)

  // 卸载时清理 timer
  useEffect(() => {
    return () => { if (hideTimer.current) clearTimeout(hideTimer.current) }
  }, [])

  const showTooltip = useCallback(() => {
    if (hideTimer.current) clearTimeout(hideTimer.current)
    setHovered(true)
  }, [])

  const hideTooltip = useCallback(() => {
    hideTimer.current = setTimeout(() => setHovered(false), 150)
  }, [])

  return (
    <>
      <span
        ref={chipRef}
        onMouseEnter={showTooltip}
        onMouseLeave={hideTooltip}
        className={[
          'inline-flex items-center gap-1 px-2 py-0.5 rounded-full',
          'text-[11px] font-semibold border cursor-default select-none',
          'mx-0.5 align-middle transition-colors duration-150',
          inOrangeBubble
            ? 'bg-white/20 text-white border-white/30 hover:bg-white/30'
            : getChipColorClass(ext),
        ].join(' ')}
      >
        <span className="text-[10px] leading-none">{getFileIcon(ext)}</span>
        <span className="max-w-[130px] truncate">{label}</span>
      </span>

      {hovered && typeof document !== 'undefined' && (
        <FileTooltip
          name={label}
          ext={ext}
          size={size}
          path={path}
          workspaceId={workspaceId}
          anchorRef={chipRef as RefObject<HTMLElement>}
          onMouseEnter={showTooltip}
          onMouseLeave={hideTooltip}
        />
      )}
    </>
  )
}

// ─── MentionNodeView — Tiptap mention 节点的 React 渲染 ──────────────────────

function MentionNodeView({ node }: NodeViewProps) {
  const { activeWorkspaceId } = useWorkspaceStore()
  const label = node.attrs.label as string
  const path  = node.attrs.id as string
  const size  = (node.attrs.size as number) ?? 0

  return (
    <NodeViewWrapper as="span" style={{ display: 'inline' }}>
      <FileChip
        label={label}
        path={path}
        size={size}
        workspaceId={activeWorkspaceId ?? undefined}
      />
    </NodeViewWrapper>
  )
}

// ─── MentionList — @ 建议下拉列表 ────────────────────────────────────────────

interface MentionItem {
  id: string; label: string; ext: string; path: string; size: number
}

function MentionList({
  items,
  command,
}: {
  items: MentionItem[]
  command: (item: MentionItem) => void
}) {
  const [selected, setSelected] = useState(0)
  useEffect(() => { setSelected(0) }, [items])

  // capture=true：在 Tiptap handleKeyDown 之前拦截，避免 Enter 选中后又触发发送
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); e.stopPropagation(); setSelected(s => Math.min(s + 1, items.length - 1)) }
      if (e.key === 'ArrowUp')   { e.preventDefault(); e.stopPropagation(); setSelected(s => Math.max(s - 1, 0)) }
      if (e.key === 'Enter')     { e.preventDefault(); e.stopPropagation(); if (items[selected]) command(items[selected]) }
    }
    document.addEventListener('keydown', handler, true)
    return () => document.removeEventListener('keydown', handler, true)
  }, [items, selected, command])

  if (!items.length) {
    return (
      <div className="bg-white rounded-xl shadow-xl border border-gray-100 p-3 text-xs text-gray-400 w-52 text-center">
        无匹配文件
      </div>
    )
  }

  return (
    <div className="bg-white rounded-xl shadow-xl border border-gray-100 py-1 max-h-56 overflow-y-auto w-56">
      <p className="px-3 pt-1.5 pb-0.5 text-[9px] font-bold text-gray-300 uppercase tracking-widest">
        工作区文件
      </p>
      {items.map((item, i) => (
        <button
          key={item.id}
          onClick={() => command(item)}
          className={[
            'w-full flex items-center gap-2.5 px-3 py-2 text-xs transition-colors text-left',
            i === selected ? 'bg-orange-50' : 'hover:bg-gray-50',
          ].join(' ')}
        >
          <span className="shrink-0 text-base leading-none">{getFileIcon(item.ext)}</span>
          <div className="flex-1 min-w-0">
            <p className={['truncate font-medium', i === selected ? 'text-orange-700' : 'text-gray-700'].join(' ')}>
              {item.label}
            </p>
            <p className="text-[9px] text-gray-400 font-mono mt-0.5">
              {getFileTypeLabel(item.ext)} · {fmtFileSize(item.size)}
            </p>
          </div>
          {i === selected && (
            <span className="shrink-0 text-[9px] text-orange-400 font-medium">↵</span>
          )}
        </button>
      ))}
    </div>
  )
}

// ─── Mention 扩展（带 NodeView + size attr）──────────────────────────────────

function buildMentionExtension(getFiles: () => WorkspaceFile[]) {
  return Mention.extend({
    addAttributes() {
      return {
        ...this.parent?.(),
        size: { default: 0 },
      }
    },
    addNodeView() {
      return ReactNodeViewRenderer(MentionNodeView)
    },
    renderHTML({ HTMLAttributes }) {
      return ['span', mergeAttributes(HTMLAttributes, { 'data-mention': '' }), 0]
    },
  }).configure({
    HTMLAttributes: {},
    suggestion: {
      items: ({ query }: { query: string }) => {
        const q = query.toLowerCase()
        return getFiles()
          .filter(f => f.name.toLowerCase().includes(q))
          .slice(0, 12)
          .map(f => ({ id: f.path, label: f.name, ext: f.ext, path: f.path, size: f.size }))
      },
      render: () => {
        let component: ReactRenderer | null = null
        let popup: TippyInstance[] | null = null
        return {
          onStart: (props: Record<string, unknown>) => {
            component = new ReactRenderer(MentionList, {
              props,
              editor: props.editor as Editor,
            })
            if (!props.clientRect) return
            popup = tippy('body', {
              getReferenceClientRect: props.clientRect as () => DOMRect,
              appendTo: () => document.body,
              content: component.element,
              showOnCreate: true,
              interactive: true,
              trigger: 'manual',
              placement: 'top-start',
              theme: 'light-border',
              offset: [0, 8],
            })
          },
          onUpdate: (props: Record<string, unknown>) => {
            component?.updateProps(props)
            if (!props.clientRect) return
            popup?.[0]?.setProps({ getReferenceClientRect: props.clientRect as () => DOMRect })
          },
          onKeyDown: (props: { event: KeyboardEvent }) => {
            if (props.event.key === 'Escape') { popup?.[0]?.hide(); return true }
            return false
          },
          onExit: () => {
            popup?.[0]?.destroy()
            component?.destroy()
          },
        }
      },
    },
  })
}

// ─── 导出工具函数 ─────────────────────────────────────────────────────────────

export interface FileRef {
  label: string
  path:  string
  size:  number
  ext:   string
}

export function extractFileRefs(editor: Editor): FileRef[] {
  const refs: FileRef[] = []
  if (!editor?.state) return refs
  editor.state.doc.descendants((node) => {
    if (node.type.name === 'mention') {
      refs.push({
        label: node.attrs.label as string,
        path:  node.attrs.id as string,
        size:  (node.attrs.size as number) ?? 0,
        ext:   getFileExt(node.attrs.label as string),
      })
    }
  })
  return refs
}

export function getPlainText(editor: Editor): string {
  let text = ''
  if (!editor?.state) return text
  editor.state.doc.descendants((node) => {
    if (node.type.name === 'text') {
      text += node.text ?? ''
    } else if (node.type.name === 'mention') {
      text += `[@${node.attrs.label as string}]`
    } else if (node.type.name === 'paragraph' && text.length > 0) {
      text += '\n'
    }
  })
  return text.trim()
}

// ─── RichInput 主组件 ─────────────────────────────────────────────────────────

export interface RichInputProps {
  disabled?: boolean
  placeholder?: string
  /** 发送回调：text 是含 [@文件名] 的纯文本，fileRefs 是解析出的引用列表 */
  onSend: (text: string, fileRefs: FileRef[]) => void
  onPaste?: (e: ClipboardEvent) => void
  /** 外部可通过此 ref 向编辑器插入文本（用于快捷回复按钮） */
  insertTextRef?: MutableRefObject<((text: string) => void) | null>
  /** 外部可通过此 ref 触发发送（用于发送按钮） */
  sendRef?: MutableRefObject<(() => void) | null>
  /** 外部可通过此 ref 插入 @mention 节点（上传完成后自动引用文件） */
  insertMentionRef?: MutableRefObject<((path: string, label: string, size: number) => void) | null>
  /** 图片上传状态回调（上传中/完成/失败） */
  onImageUploadStatus?: (status: 'uploading' | 'done' | 'error', name?: string) => void
}

export function RichInput({ disabled, placeholder, onSend, onPaste, insertTextRef, sendRef, insertMentionRef, onImageUploadStatus }: RichInputProps) {
  const { activeWorkspaceId } = useWorkspaceStore()
  const wsFilesRef = useRef<WorkspaceFile[]>([])
  const [imgUploading, setImgUploading] = useState(false)

  // ⚠️ editorRef 必须在 useEditor 之前声明，handleImagePaste / handleKeyDown 都依赖它
  const editorRef = useRef<Editor | null>(null)

  // doSend 用 ref 存储，避免 handleKeyDown 闭包陷阱
  const onSendRef = useRef(onSend)
  useEffect(() => { onSendRef.current = onSend }, [onSend])
  const disabledRef = useRef(disabled)
  useEffect(() => { disabledRef.current = disabled }, [disabled])

  // ─── 图片粘贴上传 ────────────────────────────────────────────────────────────
  // 将图片 File 上传到工作区 shared/images/ 目录，成功后自动插入 @文件名 mention
  const handleImagePaste = useCallback(async (file: File) => {
    if (!activeWorkspaceId || !editorRef.current) return

    // 生成唯一文件名：原始名_时间戳.ext（避免同名覆盖）
    const ts = Date.now()
    const rawName = file.name && file.name !== 'image.png' ? file.name : `screenshot_${ts}.png`
    const dotIdx = rawName.lastIndexOf('.')
    const stem = dotIdx > 0 ? rawName.slice(0, dotIdx) : rawName
    const ext  = dotIdx > 0 ? rawName.slice(dotIdx) : '.png'
    const safeStem = stem.replace(/[^a-zA-Z0-9_\-\u4e00-\u9fa5]/g, '_')
    const fileName = `${safeStem}_${ts}${ext}`
    const destPath = `shared/images/${fileName}`

    setImgUploading(true)
    onImageUploadStatus?.('uploading', fileName)
    try {
      // 使用 FileReader 方式上传（修复大文件 btoa spread 栈溢出）
      const b64 = await fileToBase64(file)
      await writeWorkspaceFile(activeWorkspaceId, destPath, b64, 'base64')

      // 刷新文件列表缓存
      const files = await listWorkspaceFiles(activeWorkspaceId)
      wsFilesRef.current = files
      const uploaded = files.find(f => f.path === destPath)

      // 插入 mention 节点
      editorRef.current?.chain().focus().insertContent({
        type: 'mention',
        attrs: { id: destPath, label: fileName, size: uploaded?.size ?? file.size },
      }).insertContent(' ').run()
      onImageUploadStatus?.('done', fileName)
    } catch (err) {
      console.error('[图片上传失败]', err)
      onImageUploadStatus?.('error', fileName)
    } finally {
      setImgUploading(false)
    }
  }, [activeWorkspaceId, onImageUploadStatus])

  const doSend = useCallback((editorInstance: Editor) => {
    if (disabledRef.current) return
    // 防御：editor 初始化期间 state 可能尚未就绪
    if (!editorInstance?.state) return
    const text = getPlainText(editorInstance)
    if (!text.trim()) return
    const refs = extractFileRefs(editorInstance)
    onSendRef.current(text, refs)
    editorInstance.commands.clearContent()
  }, [])

  const editor = useEditor({
    extensions: [
      StarterKit.configure({ heading: false, blockquote: false, code: false, codeBlock: false }),
      buildMentionExtension(() => wsFilesRef.current),
    ],
    editorProps: {
      attributes: {
        class: 'outline-none min-h-[60px] max-h-[200px] overflow-y-auto text-sm text-gray-700 leading-relaxed',
      },
      // ⚠️ handleKeyDown 里的 view.state 不是 Editor 实例，不能传给 doSend
      // Enter 发送已由下方独立 useEffect 的 DOM 事件监听处理，此处只处理 paste
      handlePaste: (_view, event) => {
        // 优先检测图片粘贴
        const items = Array.from(event.clipboardData?.items ?? []) as DataTransferItem[]
        const imageItem = items.find(it => it.kind === 'file' && it.type.startsWith('image/'))
        if (imageItem) {
          event.preventDefault()
          const file = imageItem.getAsFile()
          if (file) void handleImagePaste(file)
          return true
        }
        if (onPaste) onPaste(event as unknown as ClipboardEvent)
        return false
      },
    },
    content: '',
  })

  // editor 变化时同步到 ref
  useEffect(() => { editorRef.current = editor }, [editor])

  // 重新绑定 handleKeyDown 用 editorRef
  useEffect(() => {
    if (!editor) return
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Enter' && !event.shiftKey) {
        // 如果 mention 建议弹窗打开，交给 MentionList 处理
        // 通过检查 tippy 实例是否存在来判断
        const tippyVisible = document.querySelector('[data-tippy-root]')
        if (tippyVisible) return
        event.preventDefault()
        if (editorRef.current) doSend(editorRef.current)
      }
    }
    const dom = editor.view.dom
    dom.addEventListener('keydown', handleKeyDown)
    return () => dom.removeEventListener('keydown', handleKeyDown)
  }, [editor, doSend])

  // 加载工作区文件（只加载一次，workspaceId 变化时重新加载）
  useEffect(() => {
    if (!activeWorkspaceId) return
    let cancelled = false
    listWorkspaceFiles(activeWorkspaceId)
      .then(files => { if (!cancelled) wsFilesRef.current = files })
      .catch(() => {})
    return () => { cancelled = true }
  }, [activeWorkspaceId])

  // 监听全局文件引用事件
  useEffect(() => {
    const handler = (e: Event) => {
      const file = (e as CustomEvent).detail as WorkspaceFile & { workspaceId: string }
      if (!editorRef.current) return
      editorRef.current.chain().focus().insertContent({
        type: 'mention',
        attrs: { id: file.path, label: file.name, size: file.size },
      }).insertContent(' ').run()
    }
    window.addEventListener(FILE_REF_EVENT, handler)
    return () => window.removeEventListener(FILE_REF_EVENT, handler)
  }, [])

  // 暴露 insertText 给外部（快捷回复按钮）
  useEffect(() => {
    if (!insertTextRef) return
    insertTextRef.current = (text: string) => {
      if (!editorRef.current) return
      editorRef.current.chain().focus().insertContent(text).run()
    }
  }, [insertTextRef])

  // 暴露 send 给外部（发送按钮直接调用，避免 DOM 事件不可靠的问题）
  useEffect(() => {
    if (!sendRef) return
    sendRef.current = () => {
      if (editorRef.current) doSend(editorRef.current)
    }
  }, [sendRef, doSend])

  // 暴露 insertMention 给外部（上传完成后自动插入 @mention 节点）
  useEffect(() => {
    if (!insertMentionRef) return
    insertMentionRef.current = (path: string, label: string, size: number) => {
      if (!editorRef.current) return
      editorRef.current.chain().focus().insertContent({
        type: 'mention',
        attrs: { id: path, label, size },
      }).insertContent(' ').run()
    }
  }, [insertMentionRef])

  // 用 tiptap 原生 isEmpty 判断（比自解析文本更准确，mention 节点也算有内容）
  const isEmpty = !editor || editor.isEmpty

  return (
    <div className="flex items-end gap-2">
      <button
        type="button"
        className="shrink-0 w-6 h-6 flex items-center justify-center text-gray-300 hover:text-orange-400 transition-colors"
        title="粘贴文件或输入 @ 引用工作区文件"
        onClick={() => editorRef.current?.chain().focus().run()}
      >
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
            d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
        </svg>
      </button>

      <div className="flex-1 relative">
        {isEmpty && (
          <p className="absolute top-0 left-0 text-sm text-gray-300 pointer-events-none select-none leading-relaxed">
            {placeholder ?? '发消息，@ 引用工作区文件...'}
          </p>
        )}
        <EditorContent editor={editor} />
      </div>
    </div>
  )
}
