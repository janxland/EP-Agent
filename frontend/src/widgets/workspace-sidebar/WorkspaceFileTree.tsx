'use client'

/**
 * WorkspaceFileTree — 工作区文件树（树形目录版）
 *
 * 设计：
 *   - 将平铺文件列表按 path 中的 / 构建成树形节点结构
 *   - 文件夹节点支持展开/收起，默认展开第一层
 *   - Modal 基础组件统一 overlay/keyboard 逻辑
 *   - useFileOp hook 统一 op → reload 副作用模式
 *   - 图片 hover 预览通过 CSS group-hover 实现，零 JS
 *   - 刷新来源合并：fileTreeRefreshToken + ep:workspace-refresh → 同一 load
 */

import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from 'react'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import {
  listWorkspaceFiles,
  readWorkspaceFile,
  deleteWorkspaceFile,
  copyWorkspaceFile,
  renameWorkspaceFile,
  uploadFileToWorkspace,
  getFileIcon,
  getFileRawUrl,
  getFileDownloadUrl,
  fmtFileSize,
  IMAGE_EXTS,
  type WorkspaceFile,
} from '@/shared/lib/workspace-files-api'

// ─── 全局事件总线 ─────────────────────────────────────────────────────────────

export const FILE_REF_EVENT     = 'ep:file-ref'
export const FILE_PREVIEW_EVENT = 'ep:file-preview'

export const emitFileRef = (file: WorkspaceFile & { workspaceId: string; projectId?: string }) =>
  window.dispatchEvent(new CustomEvent(FILE_REF_EVENT, { detail: file }))

export const emitFilePreview = (file: WorkspaceFile & { workspaceId: string; projectId?: string }) =>
  window.dispatchEvent(new CustomEvent(FILE_PREVIEW_EVENT, { detail: file }))

// ─── 树形节点类型 ─────────────────────────────────────────────────────────────

type TreeNode =
  | { kind: 'file'; file: WorkspaceFile }
  | { kind: 'dir';  name: string; fullPath: string; children: TreeNode[] }

/** 将平铺文件列表构建为树形结构 */
function buildTree(files: WorkspaceFile[]): TreeNode[] {
  const root: TreeNode[] = []

  // 用 Map 缓存目录节点，避免重复创建
  const dirMap = new Map<string, Extract<TreeNode, { kind: 'dir' }>>()

  const getOrCreateDir = (segments: string[]): Extract<TreeNode, { kind: 'dir' }> => {
    const fullPath = segments.join('/')
    if (dirMap.has(fullPath)) return dirMap.get(fullPath)!

    const name = segments[segments.length - 1]
    const node: Extract<TreeNode, { kind: 'dir' }> = { kind: 'dir', name, fullPath, children: [] }
    dirMap.set(fullPath, node)

    if (segments.length === 1) {
      root.push(node)
    } else {
      const parent = getOrCreateDir(segments.slice(0, -1))
      parent.children.push(node)
    }
    return node
  }

  // 先按路径排序（目录在前，文件在后）
  const sorted = [...files].sort((a, b) => {
    const aDepth = a.path.split('/').length
    const bDepth = b.path.split('/').length
    if (aDepth !== bDepth) return aDepth - bDepth
    return a.path.localeCompare(b.path)
  })

  for (const file of sorted) {
    const parts = file.path.split('/')
    if (parts.length === 1) {
      // 根目录文件
      root.push({ kind: 'file', file })
    } else {
      // 确保父目录存在，然后添加文件节点
      const dirSegments = parts.slice(0, -1)
      const parentDir = getOrCreateDir(dirSegments)
      parentDir.children.push({ kind: 'file', file })
    }
  }

  // 对每个目录：目录在前，文件在后，同类按名称排序
  const sortChildren = (nodes: TreeNode[]) => {
    nodes.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'dir' ? -1 : 1
      const aName = a.kind === 'dir' ? a.name : a.file.name
      const bName = b.kind === 'dir' ? b.name : b.file.name
      return aName.localeCompare(bName)
    })
    for (const n of nodes) {
      if (n.kind === 'dir') sortChildren(n.children)
    }
  }
  sortChildren(root)

  return root
}

/** 收集所有目录路径（用于默认展开第一层） */
function collectFirstLevelDirs(nodes: TreeNode[]): Set<string> {
  const result = new Set<string>()
  for (const n of nodes) {
    if (n.kind === 'dir') result.add(n.fullPath)
  }
  return result
}

// ─── Modal 基础组件 ───────────────────────────────────────────────────────────

function Modal({ onClose, children }: { onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return (
    <div
      ref={ref}
      className="fixed inset-0 z-[600] flex items-center justify-center bg-black/20 backdrop-blur-[2px]"
      onMouseDown={(e) => { if (e.target === ref.current) onClose() }}
    >
      <div className="bg-white rounded-2xl shadow-2xl border border-gray-100 overflow-hidden animate-in fade-in zoom-in-95 duration-150">
        {children}
      </div>
    </div>
  )
}

// ─── 弹窗 footer ──────────────────────────────────────────────────────────────

function ModalFooter({
  onCancel, onConfirm, confirmLabel, confirmDisabled = false, danger = false,
}: {
  onCancel: () => void
  onConfirm: () => void
  confirmLabel: string
  confirmDisabled?: boolean
  danger?: boolean
}) {
  return (
    <div className="flex border-t border-gray-100">
      <button
        onClick={onCancel}
        className="flex-1 py-3 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-50 transition-colors font-medium border-r border-gray-100"
      >
        取消
      </button>
      <button
        onClick={onConfirm}
        disabled={confirmDisabled}
        className={[
          'flex-1 py-3 text-sm font-semibold transition-colors disabled:opacity-40 disabled:cursor-not-allowed',
          danger
            ? 'text-red-500 hover:text-red-600 hover:bg-red-50'
            : 'text-orange-500 hover:text-orange-600 hover:bg-orange-50',
        ].join(' ')}
      >
        {confirmLabel}
      </button>
    </div>
  )
}

// ─── 弹窗：删除确认 ───────────────────────────────────────────────────────────

function DeleteDialog({ file, onConfirm, onCancel }: {
  file: WorkspaceFile; onConfirm: () => void; onCancel: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Enter') onConfirm() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onConfirm])
  return (
    <Modal onClose={onCancel}>
      <div className="w-72 px-5 pt-5 pb-4 flex items-start gap-3">
        <div className="shrink-0 w-9 h-9 rounded-full bg-red-50 flex items-center justify-center mt-0.5">
          <svg className="w-[18px] h-[18px] text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-800 leading-snug">删除文件</h3>
          <p className="mt-1 text-xs text-gray-500 leading-relaxed">
            确定删除 <span className="font-medium text-gray-700 break-all">{file.name}</span>？此操作无法撤销。
          </p>
          <p className="mt-1 text-[10px] text-gray-400 font-mono">{fmtFileSize(file.size)}</p>
        </div>
      </div>
      <ModalFooter onCancel={onCancel} onConfirm={onConfirm} confirmLabel="删除" danger />
    </Modal>
  )
}

// ─── 弹窗：文本输入 ───────────────────────────────────────────────────────────

function InputDialog({ title, label, initialValue, placeholder, onConfirm, onCancel, mono = false }: {
  title: string; label?: string; initialValue: string; placeholder?: string
  onConfirm: (value: string) => void; onCancel: () => void; mono?: boolean
}) {
  const [value, setValue] = useState(initialValue)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => { inputRef.current?.select() }, [])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Enter' && value.trim() && value !== initialValue) onConfirm(value.trim())
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [value, initialValue, onConfirm])
  return (
    <Modal onClose={onCancel}>
      <div className="w-80 px-5 pt-5 pb-4">
        <h3 className="text-sm font-semibold text-gray-800 mb-3">{title}</h3>
        {label && <p className="text-[10px] text-gray-400 mb-2">{label}</p>}
        <input
          ref={inputRef}
          type="text"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          placeholder={placeholder}
          className={[
            'w-full px-3 py-2 text-sm border border-gray-200 rounded-lg',
            'focus:outline-none focus:ring-2 focus:ring-orange-300 focus:border-transparent',
            mono ? 'font-mono' : '',
          ].join(' ')}
        />
      </div>
      <ModalFooter
        onCancel={onCancel}
        onConfirm={() => { if (value.trim()) onConfirm(value.trim()) }}
        confirmLabel="确认"
        confirmDisabled={!value.trim() || value === initialValue}
      />
    </Modal>
  )
}

// ─── 右键菜单 ─────────────────────────────────────────────────────────────────

type MenuItem =
  | null
  | { download: string; icon: string; label: string }
  | { icon: string; label: string; onClick: () => void; accent?: boolean; danger?: boolean }

/** 文件右键菜单 */
function ContextMenu({ x, y, file, workspaceId, onClose, onDelete, onRef, onRename, onCopy }: {
  x: number; y: number; file: WorkspaceFile; workspaceId: string
  onClose: () => void; onDelete: () => void; onRef: () => void; onRename: () => void; onCopy: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])

  const menuItems: MenuItem[] = [
    { icon: '@',   label: '引用到对话框', onClick: () => { onRef();    onClose() }, accent: true },
    null,
    { icon: '✏️',  label: '重命名',       onClick: () => { onRename(); onClose() } },
    { icon: '📋',  label: '复制',         onClick: () => { onCopy();   onClose() } },
    { download: getFileDownloadUrl(workspaceId, file.path), label: '下载', icon: '⬇' },
    null,
    { icon: '🗑',  label: '删除',         onClick: () => { onDelete(); onClose() }, danger: true },
  ]

  return (
    <div ref={ref} style={{ top: y, left: x }}
      className="fixed z-[500] bg-white rounded-xl shadow-xl border border-gray-100 py-1 w-44 text-xs"
    >
      {menuItems.map((item, i) =>
        item === null ? <div key={i} className="my-1 border-t border-gray-100" /> :
        'download' in item ? (
          <a key={i} href={item.download} download={file.name} onClick={onClose}
            className="w-full flex items-center gap-2 px-3 py-2 hover:bg-gray-50 text-gray-600 transition-colors"
          >
            <span>{item.icon}</span><span>{item.label}</span>
          </a>
        ) : (
          <button key={i} onClick={item.onClick}
            className={[
              'w-full flex items-center gap-2 px-3 py-2 transition-colors',
              item.danger  ? 'hover:bg-red-50 text-gray-400 hover:text-red-500' :
              item.accent  ? 'hover:bg-orange-50 text-gray-600 hover:text-orange-600' :
                             'hover:bg-gray-50 text-gray-600',
            ].join(' ')}
          >
            <span>{item.icon}</span><span>{item.label}</span>
          </button>
        )
      )}
    </div>
  )
}

/** 目录右键菜单（仅删除） */
function DirContextMenu({ x, y, dirPath, onClose, onDelete }: {
  x: number; y: number; dirPath: string
  onClose: () => void; onDelete: () => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose()
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [onClose])
  return (
    <div ref={ref} style={{ top: y, left: x }}
      className="fixed z-[500] bg-white rounded-xl shadow-xl border border-gray-100 py-1 w-40 text-xs"
    >
      <div className="px-3 py-1.5 text-[9px] text-gray-300 font-mono truncate max-w-full">{dirPath}/</div>
      <div className="my-1 border-t border-gray-100" />
      <button
        onClick={() => { onDelete(); onClose() }}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-red-50 text-gray-400 hover:text-red-500 transition-colors"
      >
        <span>🗑</span><span>删除文件夹</span>
      </button>
    </div>
  )
}

// ─── 弹窗：目录删除确认 ──────────────────────────────────────────────────────

function DeleteDirDialog({ dirPath, onConfirm, onCancel }: {
  dirPath: string; onConfirm: () => void; onCancel: () => void
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Enter') onConfirm() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onConfirm])
  return (
    <Modal onClose={onCancel}>
      <div className="w-72 px-5 pt-5 pb-4 flex items-start gap-3">
        <div className="shrink-0 w-9 h-9 rounded-full bg-red-50 flex items-center justify-center mt-0.5">
          <svg className="w-[18px] h-[18px] text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
          </svg>
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-800 leading-snug">删除文件夹</h3>
          <p className="mt-1 text-xs text-gray-500 leading-relaxed">
            确定删除文件夹 <span className="font-medium text-gray-700 break-all">{dirPath}/</span>？
          </p>
          <p className="mt-1 text-[10px] text-red-400">文件夹内所有文件将一并删除，无法撤销。</p>
        </div>
      </div>
      <ModalFooter onCancel={onCancel} onConfirm={onConfirm} confirmLabel="删除" danger />
    </Modal>
  )
}

// ─── 文件行 ───────────────────────────────────────────────────────────────────

function FileRow({ file, workspaceId, resolvedProjectId, selected, depth = 0, onClick, onContextMenu, onRef }: {
  file: WorkspaceFile
  workspaceId: string
  resolvedProjectId: string
  selected: boolean
  depth?: number
  onClick: () => void
  onContextMenu: (e: ReactMouseEvent) => void
  onRef: () => void
}) {
  const isImg = IMAGE_EXTS.has(file.ext)
  const isAbc = file.ext === 'abc'
  return (
    <div
      onClick={onClick}
      onContextMenu={onContextMenu}
      style={{ paddingLeft: 8 + depth * 14 }}
      className={[
        'group relative flex items-center gap-1.5 pr-2 py-1 rounded-lg transition-colors cursor-pointer',
        selected
          ? 'bg-orange-50 text-orange-600'
          : isAbc
            ? 'hover:bg-orange-50/60'
            : 'hover:bg-gray-50',
      ].join(' ')}
      title={`${file.path} · ${fmtFileSize(file.size)}`}
    >
      <span className="shrink-0 text-[12px]">{getFileIcon(file.ext)}</span>
      <div className="flex-1 min-w-0">
        <p className={['text-[11px] truncate font-medium',
          selected ? 'text-orange-600' : isAbc ? 'text-orange-600' : 'text-gray-600'].join(' ')}>
          {file.name}
        </p>
        <p className="text-[9px] text-gray-300 font-mono">{fmtFileSize(file.size)}</p>
      </div>
      {/* 快捷引用 */}
      <button
        onClick={(e) => { e.stopPropagation(); onRef() }}
        title="引用到对话框"
        className="shrink-0 opacity-0 group-hover:opacity-100 w-5 h-5 flex items-center justify-center rounded text-gray-300 hover:text-orange-400 hover:bg-orange-50 transition-all text-[10px] font-bold"
      >@</button>
      {/* 图片 hover 预览浮层（纯 CSS） */}
      {isImg && (
        <div className="pointer-events-none absolute left-full top-1/2 -translate-y-1/2 ml-2 z-[600] hidden group-hover:block">
          <div className="bg-white rounded-xl shadow-2xl border border-gray-100 p-1.5 w-48">
            <img src={getFileRawUrl(workspaceId, file.path, resolvedProjectId)} alt={file.name}
              className="w-full h-auto rounded-lg object-contain max-h-40" loading="lazy" />
            <p className="mt-1 text-[9px] text-gray-400 text-center truncate px-1">{file.name}</p>
          </div>
        </div>
      )}
    </div>
  )
}

// ─── 目录行 ───────────────────────────────────────────────────────────────────

function DirRow({ name, fullPath, depth, expanded, onToggle, onContextMenu }: {
  name: string
  fullPath: string
  depth: number
  expanded: boolean
  onToggle: () => void
  onContextMenu: (e: ReactMouseEvent) => void
}) {
  const isSky = fullPath === '.sky' || name === '.sky'
  return (
    <button
      onClick={onToggle}
      onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onContextMenu(e) }}
      style={{ paddingLeft: 6 + depth * 14 }}
      className={[
        'w-full flex items-center gap-1.5 pr-2 py-1 rounded-lg transition-colors text-left group',
        isSky ? 'hover:bg-orange-50/60' : 'hover:bg-gray-50',
      ].join(' ')}
    >
      {/* 展开箭头 */}
      <svg
        className={['w-2.5 h-2.5 shrink-0 text-gray-300 transition-transform duration-150', expanded ? 'rotate-90' : ''].join(' ')}
        fill="none" stroke="currentColor" viewBox="0 0 24 24"
      >
        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
      </svg>
      {/* 文件夹图标 */}
      <span className="shrink-0 text-[12px]">{isSky ? '🎵' : expanded ? '📂' : '📁'}</span>
      <span className={[
        'text-[11px] font-semibold truncate flex-1',
        isSky ? 'text-orange-500' : 'text-gray-500',
      ].join(' ')}>
        {name}
      </span>
      {/* hover 时显示删除按钮 */}
      <span
        role="button"
        onClick={(e) => { e.stopPropagation(); onContextMenu(e as unknown as ReactMouseEvent) }}
        title="删除文件夹"
        className="shrink-0 opacity-0 group-hover:opacity-100 w-4 h-4 flex items-center justify-center rounded text-gray-300 hover:text-red-400 hover:bg-red-50 transition-all text-[10px]"
      >🗑</span>
    </button>
  )
}

// ─── 递归树节点渲染 ───────────────────────────────────────────────────────────

function TreeNodeRow({ node, depth, expanded, onToggle, workspaceId, resolvedProjectId, selectedPath, onFileClick, onContextMenu, onDirContextMenu, onRef }: {
  node: TreeNode
  depth: number
  expanded: Set<string>
  onToggle: (path: string) => void
  workspaceId: string
  resolvedProjectId: string
  selectedPath: string | null
  onFileClick: (file: WorkspaceFile) => void
  onContextMenu: (e: ReactMouseEvent, file: WorkspaceFile) => void
  onDirContextMenu: (e: ReactMouseEvent, dirPath: string) => void
  onRef: (file: WorkspaceFile) => void
}) {
  if (node.kind === 'file') {
    return (
      <FileRow
        file={node.file}
        workspaceId={workspaceId}
        resolvedProjectId={resolvedProjectId}
        selected={selectedPath === node.file.path}
        depth={depth}
        onClick={() => onFileClick(node.file)}
        onContextMenu={(e) => { e.preventDefault(); e.stopPropagation(); onContextMenu(e, node.file) }}
        onRef={() => onRef(node.file)}
      />
    )
  }

  const isExpanded = expanded.has(node.fullPath)
  return (
    <div>
      <DirRow
        name={node.name}
        fullPath={node.fullPath}
        depth={depth}
        expanded={isExpanded}
        onToggle={() => onToggle(node.fullPath)}
        onContextMenu={(e) => onDirContextMenu(e, node.fullPath)}
      />
      {isExpanded && (
        <div>
          {node.children.map((child, i) => (
            <TreeNodeRow
              key={child.kind === 'file' ? child.file.path : child.fullPath + i}
              node={child}
              depth={depth + 1}
              expanded={expanded}
              onToggle={onToggle}
              workspaceId={workspaceId}
              resolvedProjectId={resolvedProjectId}
              selectedPath={selectedPath}
              onFileClick={onFileClick}
              onContextMenu={onContextMenu}
              onDirContextMenu={onDirContextMenu}
              onRef={onRef}
            />
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 主组件 ───────────────────────────────────────────────────────────────────

type DialogState =
  | { type: 'delete' | 'rename' | 'copy'; file: WorkspaceFile }
  | { type: 'delete-dir'; dirPath: string }
  | null

export function WorkspaceFileTree() {
  const { activeWorkspaceId, activeProjectId, activeProject, fileTreeRefreshToken, workspaces } = useWorkspaceStore()
  // 切换工作区后 activeProjectId 自动同步，此处再加兜底确保始终有效。
  // 同时订阅 workspaces，确保 workspaces 异步加载完成后 activeProject() 能正确返回项目 ID。
  // 场景：session 刚创建时 activeProjectId 有值但 workspaces 为空，activeProject() 返回 null；
  // 等 loadWorkspaces 完成后 workspaces 更新，此处重新计算 resolvedProjectId，触发 load。
  const resolvedProjectId = activeProjectId
    ?? activeProject()?.id
    ?? workspaces.find(w => w.id === activeWorkspaceId)?.projects?.[0]?.id
    ?? ''

  // ── 关键修复：用 ref 存储最新的 wsId + projId，确保 load 闭包始终读到最新值 ──
  // 问题根因：load 是 useCallback，依赖 [activeWorkspaceId, resolvedProjectId]。
  // 但 restoreFromSessionId 是异步的，首次渲染时 resolvedProjectId 可能为空，
  // load 已经用空 projId 跑完了。之后 store 更新触发 re-render，新 load 才有正确 projId，
  // 但 fileTreeRefreshToken/ep:workspace-refresh 触发的是旧闭包里的 load（空 projId）。
  // 解法：load 内部通过 ref 读取最新值，彻底解耦闭包与 store 状态更新时序。
  const wsIdRef    = useRef(activeWorkspaceId)
  const projIdRef  = useRef(resolvedProjectId)
  useEffect(() => { wsIdRef.current   = activeWorkspaceId  }, [activeWorkspaceId])
  useEffect(() => { projIdRef.current = resolvedProjectId  }, [resolvedProjectId])

  const [files, setFiles]       = useState<WorkspaceFile[]>([])
  const [loading, setLoading]   = useState(false)
  const [error, setError]       = useState<string | null>(null)
  const [dragging, setDragging] = useState(false)
  const [selectedPath, setSelectedPath] = useState<string | null>(null)
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [menu, setMenu]         = useState<{ x: number; y: number; file: WorkspaceFile } | null>(null)
  const [dirMenu, setDirMenu]   = useState<{ x: number; y: number; dirPath: string } | null>(null)
  const [dialog, setDialog]     = useState<DialogState>(null)
  const fileInputRef            = useRef<HTMLInputElement>(null)

  // load 始终从 ref 读取最新的 wsId/projId，不依赖闭包捕获的旧值
  // 这样无论是 fileTreeRefreshToken、ep:workspace-refresh 还是手动刷新，
  // 都能用到 restoreFromSessionId 异步完成后的最新 project_id
  const load = useCallback(async () => {
    const wsId   = wsIdRef.current
    const projId = projIdRef.current
    if (!wsId) return
    setLoading(true); setError(null)
    try {
      const loaded = await listWorkspaceFiles(wsId, projId)
      setFiles(loaded)
      // 首次加载时默认展开第一层目录
      setExpanded(prev => {
        if (prev.size > 0) return prev  // 已有展开状态，保留用户操作
        const tree = buildTree(loaded)
        return collectFirstLevelDirs(tree)
      })
    }
    catch (e) { setError(e instanceof Error ? e.message : '加载失败') }
    finally   { setLoading(false) }
  }, []) // 空依赖：load 自身稳定，通过 ref 获取最新值

  // ── 触发加载的三条路径 ────────────────────────────────────────────────────────

  // 路径 1：wsId 或 projId 变化时重新加载
  // 用独立 effect 监听，确保 restoreFromSessionId 异步完成后能触发
  useEffect(() => {
    if (activeWorkspaceId) void load()
  }, [activeWorkspaceId, resolvedProjectId, load])

  // 路径 2：fileTreeRefreshToken 递增（文件上传/工具写文件后触发）
  useEffect(() => { if (fileTreeRefreshToken > 0) void load() }, [fileTreeRefreshToken, load])

  // 路径 3：ep:workspace-refresh 全局事件（SSE tool.call 成功后触发）
  useEffect(() => {
    window.addEventListener('ep:workspace-refresh', load as EventListener)
    return () => window.removeEventListener('ep:workspace-refresh', load as EventListener)
  }, [load])

  // 切换工作区或项目时重置展开状态（保证新项目展开第一层）
  useEffect(() => { setExpanded(new Set()) }, [activeWorkspaceId, activeProjectId])

  const withOp = useCallback(async (op: () => Promise<void>, errMsg: string) => {
    try   { await op(); await load() }
    catch (e) { setError(e instanceof Error ? e.message : errMsg) }
  }, [load])

  const handleUpload = useCallback(async (fileList: FileList | null) => {
    if (!fileList || !activeWorkspaceId) return
    setLoading(true)
    await withOp(
      () => Promise.all(Array.from(fileList).map(f => uploadFileToWorkspace(activeWorkspaceId, f, undefined, resolvedProjectId))).then(() => {}),
      '上传失败'
    )
    setLoading(false)
  }, [activeWorkspaceId, resolvedProjectId, withOp])

  const handleFileClick = useCallback(async (file: WorkspaceFile) => {
    if (!activeWorkspaceId) return
    setSelectedPath(file.path)
    if (file.ext === 'abc') {
      try {
        const abc = await readWorkspaceFile(activeWorkspaceId, file.path, resolvedProjectId)
        window.dispatchEvent(new CustomEvent('ep:load-score', { detail: { abc, path: file.path, name: file.name } }))
      } catch { /* ignore */ }
    }
    emitFilePreview({ ...file, workspaceId: activeWorkspaceId, projectId: resolvedProjectId || undefined })
  }, [activeWorkspaceId, resolvedProjectId])

  const toggleDir = useCallback((path: string) => {
    setExpanded(prev => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }, [])

  // 构建树形结构（memo 缓存，files 变化时才重算）
  const tree = useMemo(() => buildTree(files), [files])

  if (!activeWorkspaceId) return (
    <div className="p-3 text-center text-[10px] text-gray-300">请先选择工作区</div>
  )

  return (
    <div
      className={['flex flex-col h-full select-none transition-colors', dragging ? 'bg-orange-50/60' : ''].join(' ')}
      onDragOver={(e) => { e.preventDefault(); setDragging(true) }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => { e.preventDefault(); setDragging(false); void handleUpload(e.dataTransfer.files) }}
    >
      {/* 头部 */}
      <div className="flex items-center justify-between px-3 pt-2.5 pb-1 shrink-0">
        <span className="text-[10px] font-bold text-gray-400 uppercase tracking-widest">项目文件</span>
        <div className="flex items-center gap-1">
          {loading && (
            <svg className="w-3 h-3 animate-spin text-orange-300" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          )}
          <button onClick={() => void load()} title="刷新"
            className="w-5 h-5 flex items-center justify-center rounded hover:bg-gray-100 text-gray-300 hover:text-gray-500 transition-colors"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
          <button onClick={() => fileInputRef.current?.click()}
            title="上传文件"
            className="w-5 h-5 flex items-center justify-center rounded hover:bg-orange-50 text-gray-300 hover:text-orange-400 transition-colors"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-8l-4-4m0 0L8 8m4-4v12" />
            </svg>
          </button>
          <input ref={fileInputRef} type="file" multiple className="hidden"
            onChange={(e) => void handleUpload(e.target.files)} />
        </div>
      </div>

      {/* 错误提示 */}
      {error && (
        <div className="mx-2 mb-1 px-2 py-1 bg-red-50 text-red-400 text-[10px] rounded-lg flex items-center gap-1">
          <span>⚠</span>
          <span className="flex-1 truncate">{error}</span>
          <button onClick={() => setError(null)}>✕</button>
        </div>
      )}

      {/* 文件树 */}
      <div className="flex-1 overflow-y-auto px-1.5 pb-2">
        {files.length === 0 && !loading ? (
          <div className="py-6 flex flex-col items-center gap-2 text-center">
            <span className="text-2xl opacity-20">📁</span>
            <p className="text-[10px] text-gray-300 leading-relaxed">
              拖拽文件上传<br />或点击右上角按钮
            </p>
          </div>
        ) : (
          <div className="space-y-0.5 pt-0.5">
            {tree.map((node, i) => (
              <TreeNodeRow
                key={node.kind === 'file' ? node.file.path : node.fullPath + i}
                node={node}
                depth={0}
                expanded={expanded}
                onToggle={toggleDir}
                workspaceId={activeWorkspaceId}
                resolvedProjectId={resolvedProjectId}
                selectedPath={selectedPath}
                onFileClick={handleFileClick}
                onContextMenu={(e, file) => setMenu({ x: e.clientX, y: e.clientY, file })}
                onDirContextMenu={(e, dirPath) => setDirMenu({ x: e.clientX, y: e.clientY, dirPath })}
                onRef={(file) => emitFileRef({ ...file, workspaceId: activeWorkspaceId })}
              />
            ))}
          </div>
        )}
      </div>

      {/* 拖拽遮罩 */}
      {dragging && (
        <div className="absolute inset-0 flex items-center justify-center bg-orange-50/80 rounded-xl border-2 border-dashed border-orange-300 z-10 pointer-events-none">
          <p className="text-xs text-orange-500 font-medium">松开以上传</p>
        </div>
      )}

      {/* 文件右键菜单 */}
      {menu && (
        <ContextMenu
          x={menu.x} y={menu.y} file={menu.file} workspaceId={activeWorkspaceId}
          onClose={() => setMenu(null)}
          onDelete={() => { setDialog({ type: 'delete', file: menu.file }); setMenu(null) }}
          onRef={() => emitFileRef({ ...menu.file, workspaceId: activeWorkspaceId })}
          onRename={() => { setDialog({ type: 'rename', file: menu.file }); setMenu(null) }}
          onCopy={() => { setDialog({ type: 'copy', file: menu.file }); setMenu(null) }}
        />
      )}

      {/* 目录右键菜单 */}
      {dirMenu && (
        <DirContextMenu
          x={dirMenu.x} y={dirMenu.y} dirPath={dirMenu.dirPath}
          onClose={() => setDirMenu(null)}
          onDelete={() => { setDialog({ type: 'delete-dir', dirPath: dirMenu.dirPath }); setDirMenu(null) }}
        />
      )}

      {/* 弹窗 */}
      {dialog?.type === 'delete' && (
        <DeleteDialog
          file={dialog.file}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            const f = dialog.file; setDialog(null)
            void withOp(() => deleteWorkspaceFile(activeWorkspaceId, f.path, resolvedProjectId), '删除失败')
          }}
        />
      )}
      {dialog?.type === 'delete-dir' && (
        <DeleteDirDialog
          dirPath={dialog.dirPath}
          onCancel={() => setDialog(null)}
          onConfirm={() => {
            const p = dialog.dirPath; setDialog(null)
            void withOp(() => deleteWorkspaceFile(activeWorkspaceId, p, resolvedProjectId), '删除文件夹失败')
          }}
        />
      )}
      {dialog?.type === 'rename' && (
        <InputDialog
          title="重命名文件"
          label={`原文件名：${dialog.file.name}`}
          initialValue={dialog.file.name}
          placeholder="输入新文件名"
          onCancel={() => setDialog(null)}
          onConfirm={(newName) => {
            const f = dialog.file; setDialog(null)
            void withOp(() => renameWorkspaceFile(activeWorkspaceId, f.path, newName, resolvedProjectId), '重命名失败')
          }}
        />
      )}
      {dialog?.type === 'copy' && (
        <InputDialog
          title="复制文件"
          label={`来源：${dialog.file.path}`}
          initialValue={(() => {
            const { path } = dialog.file
            const dot = path.lastIndexOf('.'), slash = path.lastIndexOf('/')
            return dot > slash ? path.slice(0, dot) + '_copy' + path.slice(dot) : path + '_copy'
          })()}
          placeholder="目标路径（含文件名）"
          mono
          onCancel={() => setDialog(null)}
          onConfirm={(dst) => {
            const f = dialog.file; setDialog(null)
            void withOp(() => copyWorkspaceFile(activeWorkspaceId, f.path, dst, resolvedProjectId), '复制失败')
          }}
        />
      )}
    </div>
  )
}
