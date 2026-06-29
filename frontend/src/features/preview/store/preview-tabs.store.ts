/**
 * preview-tabs.store.ts — 预览标签页状态管理
 *
 * 设计原则：
 *   - 完全解耦：不依赖任何页面组件，任何地方都可以 dispatch 打开标签
 *   - 单一事实来源：所有标签页状态集中在此 store
 *   - 类型安全：每种标签类型有明确的 payload 类型
 *
 * 标签类型：
 *   abc      — ABC 乐谱渲染（abcNotation 字符串）
 *   file     — 工作区文件预览（html/image/text/binary）
 *   logs     — 执行日志
 *   export   — 导出面板
 *
 * 使用方式：
 *   // 任意组件/事件处理器中打开新标签
 *   usePreviewTabsStore.getState().openTab({ type: 'file', file, workspaceId })
 *   usePreviewTabsStore.getState().openTab({ type: 'abc', abc: '...', title: '曲名' })
 */

import { create } from 'zustand'
import type { WorkspaceFile } from '@/shared/lib/workspace-files-api'

// ─── 标签类型定义 ───────────────────────────────────────────────────────────────

export type AbcTab = {
  type: 'abc'
  id: string
  title: string
  abc: string
  scoreTitle?: string
}

export type FileTab = {
  type: 'file'
  id: string
  title: string
  file: WorkspaceFile
  workspaceId: string
  projectId?: string   // 项目 ID，用于构造正确的三层路径
}

export type LogsTab = {
  type: 'logs'
  id: string
  title: string
}

export type ExportTab = {
  type: 'export'
  id: string
  title: string
}

export type PreviewTab = AbcTab | FileTab | LogsTab | ExportTab

// openTab 的入参（不需要 id，store 自动生成）
export type OpenTabPayload =
  | Omit<AbcTab,   'id'>
  | Omit<FileTab,  'id'>
  | Omit<LogsTab,  'id'>
  | Omit<ExportTab,'id'>

// ─── Store 接口 ────────────────────────────────────────────────────────────────

interface PreviewTabsState {
  tabs:        PreviewTab[]
  activeTabId: string | null

  /**
   * 打开标签页。
   * - file 类型：同路径去重（已存在则激活，不重复打开）
   * - abc 类型：始终只保留一个 abc 标签（更新内容而非新增）
   * - logs/export：单例（已存在则激活）
   */
  openTab: (payload: OpenTabPayload) => void

  /** 关闭指定标签，自动激活相邻标签 */
  closeTab: (tabId: string) => void

  /** 激活指定标签 */
  activateTab: (tabId: string) => void

  /** 关闭所有标签 */
  closeAll: () => void

  /** 更新 abc 标签内容（abc 更新时调用） */
  updateAbcTab: (abc: string, title?: string) => void
}

// ─── Store 实现 ────────────────────────────────────────────────────────────────

let _idCounter = 0
const genId = () => `tab_${++_idCounter}_${Date.now()}`

// 固定 ID（单例标签）
const LOGS_TAB_ID   = 'tab_logs'
const EXPORT_TAB_ID = 'tab_export'
const ABC_TAB_ID    = 'tab_abc'

export const usePreviewTabsStore = create<PreviewTabsState>((set, get) => ({
  tabs:        [],
  activeTabId: null,

  openTab: (payload) => {
    const { tabs } = get()

    // ── abc 类型：单例，已存在则更新内容并激活 ────────────────────────────────
    if (payload.type === 'abc') {
      const existing = tabs.find(t => t.id === ABC_TAB_ID)
      if (existing) {
        set(s => ({
          tabs: s.tabs.map(t =>
            t.id === ABC_TAB_ID
              ? { ...t, abc: (payload as Omit<AbcTab,'id'>).abc, title: payload.title }
              : t
          ),
          activeTabId: ABC_TAB_ID,
        }))
      } else {
        const tab: AbcTab = { ...payload as Omit<AbcTab,'id'>, id: ABC_TAB_ID }
        set(s => ({ tabs: [...s.tabs, tab], activeTabId: ABC_TAB_ID }))
      }
      return
    }

    // ── logs/export：单例 ─────────────────────────────────────────────────────
    if (payload.type === 'logs' || payload.type === 'export') {
      const fixedId = payload.type === 'logs' ? LOGS_TAB_ID : EXPORT_TAB_ID
      const existing = tabs.find(t => t.id === fixedId)
      if (existing) {
        set({ activeTabId: fixedId })
      } else {
        const tab = { ...payload, id: fixedId } as PreviewTab
        set(s => ({ tabs: [...s.tabs, tab], activeTabId: fixedId }))
      }
      return
    }

    // ── file 类型：同路径去重 ─────────────────────────────────────────────────
    if (payload.type === 'file') {
      const p = payload as Omit<FileTab, 'id'>
      const dedupeKey = `${p.workspaceId}:${p.file.path}`
      const existing = tabs.find(t => t.type === 'file' && `${(t as FileTab).workspaceId}:${(t as FileTab).file.path}` === dedupeKey)
      if (existing) {
        set({ activeTabId: existing.id })
        return
      }
      const tab: FileTab = { ...p, id: genId() }
      set(s => ({ tabs: [...s.tabs, tab], activeTabId: tab.id }))
    }
  },

  closeTab: (tabId) => {
    const { tabs, activeTabId } = get()
    const idx = tabs.findIndex(t => t.id === tabId)
    if (idx === -1) return
    const newTabs = tabs.filter(t => t.id !== tabId)

    let newActive = activeTabId
    if (activeTabId === tabId) {
      // 优先激活右侧，其次左侧，最后 null
      newActive = newTabs[idx]?.id ?? newTabs[idx - 1]?.id ?? null
    }
    set({ tabs: newTabs, activeTabId: newActive })
  },

  activateTab: (tabId) => set({ activeTabId: tabId }),

  closeAll: () => set({ tabs: [], activeTabId: null }),

  updateAbcTab: (abc, title) => {
    const { tabs, activeTabId } = get()
    const existing = tabs.find(t => t.id === ABC_TAB_ID)
    if (!existing) {
      // 没有 abc 标签时自动创建
      const tab: AbcTab = { id: ABC_TAB_ID, type: 'abc', abc, title: title ?? '乐谱预览' }
      set(s => ({ tabs: [...s.tabs, tab], activeTabId: ABC_TAB_ID }))
    } else {
      set(s => ({
        tabs: s.tabs.map(t =>
          t.id === ABC_TAB_ID
            ? { ...t, abc, ...(title ? { title } : {}) }
            : t
        ),
        activeTabId: ABC_TAB_ID,
      }))
    }
  },
}))

// ─── 便捷 dispatch 函数（无需 hook，可在事件处理器中调用）──────────────────────

export const previewTabs = {
  openFile: (file: WorkspaceFile, workspaceId: string, projectId?: string) =>
    usePreviewTabsStore.getState().openTab({ type: 'file', title: file.name, file, workspaceId, projectId }),

  openAbc: (abc: string, title?: string, scoreTitle?: string) =>
    usePreviewTabsStore.getState().openTab({ type: 'abc', title: title ?? '乐谱预览', abc, scoreTitle }),

  openLogs: () =>
    usePreviewTabsStore.getState().openTab({ type: 'logs', title: '执行日志' }),

  openExport: () =>
    usePreviewTabsStore.getState().openTab({ type: 'export', title: '导出' }),

  updateAbc: (abc: string, title?: string) =>
    usePreviewTabsStore.getState().updateAbcTab(abc, title),
}
