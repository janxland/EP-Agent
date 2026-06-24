/**
 * workspace.store.ts — 工作区全局状态管理
 *
 * 设计原则：
 *   - 工作区（Workspace）= 音乐项目，包含多个对话（Session）
 *   - localStorage 持久化：记住上次活跃的 workspaceId / sessionId，刷新后恢复
 *   - 乐观更新：本地先更新，失败时回滚
 *   - 单一事实来源：workspaces 列表 + activeWorkspaceId + activeSessionId
 */

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import {
  listWorkspaces,
  createWorkspace as apiCreateWorkspace,
  renameWorkspace as apiRenameWorkspace,
  deleteWorkspace as apiDeleteWorkspace,
  createSession as apiCreateSession,
  deleteSession as apiDeleteSession,
  renameSession as apiRenameSession,
  getSessionInfo,
} from '@/shared/lib/api'
import type { WorkspaceDto, SessionInfoDto } from '@/shared/lib/api'

// ─── 类型 ──────────────────────────────────────────────────────────────────────

export type { WorkspaceDto, SessionInfoDto }

interface WorkspaceStoreState {
  /** 所有工作区列表（含嵌套 sessions） */
  workspaces: WorkspaceDto[]
  /** 当前活跃工作区 ID */
  activeWorkspaceId: string | null
  /** 当前活跃对话 ID */
  activeSessionId: string | null
  /** 是否正在加载 */
  loading: boolean
  /** 错误信息 */
  error: string | null
  /** 侧边栏是否折叠 */
  sidebarCollapsed: boolean

  // ── 查询 ──
  /** 获取当前活跃工作区 */
  activeWorkspace: () => WorkspaceDto | null
  /** 获取当前活跃工作区下的对话列表 */
  activeSessions: () => SessionInfoDto[]

  // ── 数据加载 ──
  /** 从后端加载所有工作区（含 sessions） */
  loadWorkspaces: () => Promise<void>
  /** 刷新单个工作区的 session 列表 */
  refreshWorkspaceSessions: (wsId: string) => Promise<void>

  // ── 工作区操作 ──
  createWorkspace: (name: string, description?: string) => Promise<WorkspaceDto>
  renameWorkspace: (wsId: string, name: string) => Promise<void>
  deleteWorkspace: (wsId: string) => Promise<void>

  // ── 对话操作 ──
  createSession: (wsId: string, title?: string) => Promise<SessionInfoDto>
  renameSession: (sessionId: string, title: string) => Promise<void>
  deleteSession: (sessionId: string) => Promise<void>

  // ── 路由恢复 ──
  /** 从 URL sessionId 恢复 workspaceId（刷新后调用） */
  restoreFromSessionId: (sessionId: string) => Promise<void>

  // ── UI 状态 ──
  setActiveWorkspaceId: (id: string | null) => void
  setActiveSessionId: (id: string | null) => void
  toggleSidebar: () => void
  clearError: () => void
  /** 删除当前 session/workspace 后，页面应跳转的目标 sessionId（null=跳回 /pro，undefined=无需跳转） */
  _pendingNavigateSessionId?: string | null
  clearPendingNavigate: () => void
}

// ─── Store 实现 ────────────────────────────────────────────────────────────────

export const useWorkspaceStore = create<WorkspaceStoreState>()(
  persist(
    (set, get) => ({
      workspaces: [],
      activeWorkspaceId: null,
      activeSessionId: null,
      loading: false,
      error: null,
      sidebarCollapsed: false,

      // ── 查询 ────────────────────────────────────────────────────────────────

      activeWorkspace: () => {
        const { workspaces, activeWorkspaceId } = get()
        return workspaces.find((w) => w.id === activeWorkspaceId) ?? null
      },

      activeSessions: () => {
        const ws = get().activeWorkspace()
        return ws?.sessions ?? []
      },

      // ── 数据加载 ─────────────────────────────────────────────────────────────

      loadWorkspaces: async () => {
        // 防重入：已在加载中则跳过
        if (get().loading) return
        set({ loading: true, error: null })
        try {
          const { workspaces: raw } = await listWorkspaces()
          // 按 id 去重（防止后端或乐观更新产生重复项）
          const seen = new Set<string>()
          const workspaces = raw.filter((w) => {
            if (seen.has(w.id)) return false
            seen.add(w.id)
            return true
          })
          set({ workspaces, loading: false })
          // 若当前活跃工作区已被删除，重置
          const { activeWorkspaceId } = get()
          if (activeWorkspaceId && !workspaces.find((w) => w.id === activeWorkspaceId)) {
            set({ activeWorkspaceId: workspaces[0]?.id ?? null })
          }
        } catch (e) {
          set({ loading: false, error: String(e) })
        }
      },

      refreshWorkspaceSessions: async (_wsId: string) => {
        try {
          const { workspaces: raw } = await listWorkspaces()
          const seen = new Set<string>()
          const workspaces = raw.filter((w) => { if (seen.has(w.id)) return false; seen.add(w.id); return true })
          set({ workspaces })
        } catch (e) {
          set({ error: String(e) })
        }
      },

      // ── 工作区操作 ───────────────────────────────────────────────────────────

      createWorkspace: async (name, description = '') => {
        set({ loading: true, error: null })
        try {
          const ws = await apiCreateWorkspace(name, description)
          // 直接 reload 而非乐观插入，避免与 loadWorkspaces 产生重复项
          const { workspaces: raw } = await listWorkspaces()
          const seen = new Set<string>()
          const workspaces = raw.filter((w) => { if (seen.has(w.id)) return false; seen.add(w.id); return true })
          set({ workspaces, activeWorkspaceId: ws.id, loading: false })
          return ws
        } catch (e) {
          set({ loading: false, error: String(e) })
          throw e
        }
      },

      renameWorkspace: async (wsId, name) => {
        // 乐观更新
        set((s) => ({
          workspaces: s.workspaces.map((w) =>
            w.id === wsId ? { ...w, name } : w
          ),
        }))
        try {
          await apiRenameWorkspace(wsId, name)
        } catch (e) {
          // 回滚：重新加载
          get().loadWorkspaces()
          set({ error: String(e) })
          throw e
        }
      },

      deleteWorkspace: async (wsId) => {
        const prev = get().workspaces
        const deletedWs = prev.find((w) => w.id === wsId)
        const deletedSessionIds = new Set((deletedWs?.sessions ?? []).map((s) => s.id))
        const nextWs = prev.find((w) => w.id !== wsId) ?? null
        const { activeSessionId } = get()
        // 若当前 session 属于被删工作区，找下一个可用 session
        const nextSessionId = deletedSessionIds.has(activeSessionId ?? '')
          ? (nextWs?.sessions?.[0]?.id ?? null)
          : activeSessionId
        set((s) => ({
          workspaces: s.workspaces.filter((w) => w.id !== wsId),
          activeWorkspaceId: s.activeWorkspaceId === wsId ? (nextWs?.id ?? null) : s.activeWorkspaceId,
          activeSessionId: nextSessionId,
          _pendingNavigateSessionId: deletedSessionIds.has(activeSessionId ?? '') ? nextSessionId : undefined,
        }))
        try {
          await apiDeleteWorkspace(wsId)
        } catch (e) {
          set({ workspaces: prev, error: String(e) })
          throw e
        }
      },

      // ── 对话操作 ─────────────────────────────────────────────────────────────

      createSession: async (wsId, title = '新对话') => {
        try {
          const { session_id, workspace_id } = await apiCreateSession(wsId, title)
          const newSession: SessionInfoDto = {
            id: session_id,
            workspace_id: workspace_id ?? wsId,
            title,
            score_title: null,
            score_key: null,
            score_bpm: null,
            score_notes: null,
            pipeline_state: 'idle',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          }
          // 乐观插入到对应工作区
          set((s) => ({
            workspaces: s.workspaces.map((w) =>
              w.id === wsId
                ? { ...w, sessions: [newSession, ...(w.sessions ?? [])] }
                : w
            ),
            activeSessionId: session_id,
          }))
          return newSession
        } catch (e) {
          set({ error: String(e) })
          throw e
        }
      },

      renameSession: async (sessionId, title) => {
        // 乐观更新
        set((s) => ({
          workspaces: s.workspaces.map((w) => ({
            ...w,
            sessions: (w.sessions ?? []).map((sess) =>
              sess.id === sessionId ? { ...sess, title } : sess
            ),
          })),
        }))
        try {
          await apiRenameSession(sessionId, title)
        } catch (e) {
          // 回滚
          get().loadWorkspaces()
          set({ error: String(e) })
          throw e
        }
      },

      deleteSession: async (sessionId) => {
        const prev = get().workspaces
        const { activeSessionId } = get()
        const isActive = activeSessionId === sessionId
        // 找包含被删 session 的工作区（不依赖 activeWorkspaceId，避免跨工作区删除找不到）
        const ownerWs = prev.find((w) => w.sessions?.some((s) => s.id === sessionId))
        const nextSession = isActive
          ? (ownerWs?.sessions ?? []).find((s) => s.id !== sessionId)
            // 同工作区无其他 session 时，跨工作区找第一个可用 session
            ?? prev.flatMap((w) => w.sessions ?? []).find((s) => s.id !== sessionId)
            ?? null
          : null
        set((s) => ({
          workspaces: s.workspaces.map((w) => ({
            ...w,
            sessions: (w.sessions ?? []).filter((sess) => sess.id !== sessionId),
          })),
          activeSessionId: isActive ? (nextSession?.id ?? null) : s.activeSessionId,
          _pendingNavigateSessionId: isActive ? (nextSession?.id ?? null) : undefined,
        }))
        try {
          await apiDeleteSession(sessionId)
        } catch (e) {
          set({ workspaces: prev, error: String(e) })
          throw e
        }
      },

      // ── 路由恢复 ─────────────────────────────────────────────────────────────

      restoreFromSessionId: async (sessionId) => {
        // 先从本地查找（避免重复网络请求）
        const { workspaces } = get()
        for (const ws of workspaces) {
          if (ws.sessions?.some((s) => s.id === sessionId)) {
            set({ activeWorkspaceId: ws.id, activeSessionId: sessionId })
            return
          }
        }
        // 本地没有（刷新后列表为空）→ 先加载列表，再从列表中查
        try {
          await get().loadWorkspaces()
          const { workspaces: loaded } = get()
          for (const ws of loaded) {
            if (ws.sessions?.some((s) => s.id === sessionId)) {
              set({ activeWorkspaceId: ws.id, activeSessionId: sessionId })
              return
            }
          }
          // 列表里也没有，再单独查 session 信息
          const info = await getSessionInfo(sessionId)
          if (info.workspace_id) {
            set({ activeWorkspaceId: info.workspace_id, activeSessionId: sessionId })
          } else {
            // workspace_id 为空时，取列表第一个工作区作为兜底（侧边栏至少有高亮）
            const fallbackWsId = get().workspaces[0]?.id ?? null
            set({ activeWorkspaceId: fallbackWsId, activeSessionId: sessionId })
          }
        } catch {
          // 网络失败时也设置 activeSessionId，并用第一个工作区兜底
          const fallbackWsId = get().workspaces[0]?.id ?? null
          set({ activeWorkspaceId: fallbackWsId, activeSessionId: sessionId })
        }
      },

      // ── UI 状态 ──────────────────────────────────────────────────────────────

      setActiveWorkspaceId: (id) => set({ activeWorkspaceId: id }),
      setActiveSessionId: (id) => set({ activeSessionId: id }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      clearError: () => set({ error: null }),
      clearPendingNavigate: () => set({ _pendingNavigateSessionId: undefined }),
    }),
    {
      name: 'ep-agent-workspace',
      // 只持久化关键导航状态，不持久化列表数据（每次刷新重新加载）
      partialize: (s) => ({
        activeWorkspaceId: s.activeWorkspaceId,
        activeSessionId: s.activeSessionId,
        sidebarCollapsed: s.sidebarCollapsed,
      }),
    }
  )
)
