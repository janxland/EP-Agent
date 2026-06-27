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
  createProject as apiCreateProject,
  renameProject as apiRenameProject,
  deleteProject as apiDeleteProject,
  createSession as apiCreateSession,
  deleteSession as apiDeleteSession,
  renameSession as apiRenameSession,
  getSessionInfo,
} from '@/shared/lib/api'
import type { WorkspaceDto, ProjectDto, SessionInfoDto } from '@/shared/lib/api'

// ─── 类型 ──────────────────────────────────────────────────────────────────────

export type { WorkspaceDto, ProjectDto, SessionInfoDto }

interface WorkspaceStoreState {
  /** 所有工作区列表（含嵌套 projects → sessions 三层结构） */
  workspaces: WorkspaceDto[]
  /** 当前活跃工作区 ID */
  activeWorkspaceId: string | null
  /** 当前活跃项目 ID（三层架构：Workspace → Project → Session） */
  activeProjectId: string | null
  /** 当前活跃对话 ID */
  activeSessionId: string | null
  /** 是否正在加载 */
  loading: boolean
  /** 错误信息 */
  error: string | null
  /** 侧边栏是否折叠 */
  sidebarCollapsed: boolean
  /** 文件树刷新令牌（每次文件上传后递增，WorkspaceFileTree 监听此值触发刷新） */
  fileTreeRefreshToken: number
  /** 触发文件树刷新 */
  triggerFileTreeRefresh: () => void

  // ── 查询 ──
  /** 获取当前活跃工作区 */
  activeWorkspace: () => WorkspaceDto | null
  /** 获取当前活跃项目 */
  activeProject: () => ProjectDto | null
  /** 获取当前活跃项目下的对话列表（优先从 project.sessions，兜底从 workspace.sessions） */
  activeSessions: () => SessionInfoDto[]

  // ── 数据加载 ──
  /** 从后端加载所有工作区（含 projects → sessions 三层结构） */
  loadWorkspaces: () => Promise<void>
  /** 刷新单个工作区的 session 列表 */
  refreshWorkspaceSessions: (wsId: string) => Promise<void>

  // ── 工作区操作 ──
  createWorkspace: (name: string, description?: string) => Promise<WorkspaceDto>
  renameWorkspace: (wsId: string, name: string) => Promise<void>
  deleteWorkspace: (wsId: string) => Promise<void>

  // ── 项目操作（三层架构新增）──
  createProject: (wsId: string, name: string, description?: string) => Promise<ProjectDto>
  renameProject: (projId: string, name: string) => Promise<void>
  deleteProject: (projId: string) => Promise<void>

  // ── 对话操作 ──
  createSession: (wsId: string, title?: string, projId?: string) => Promise<SessionInfoDto>
  renameSession: (sessionId: string, title: string) => Promise<void>
  deleteSession: (sessionId: string) => Promise<void>

  // ── 路由恢复 ──
  /** 从 URL sessionId 恢复 workspaceId / projectId（刷新后调用） */
  restoreFromSessionId: (sessionId: string) => Promise<void>

  // ── UI 状态 ──
  setActiveWorkspaceId: (id: string | null) => void
  setActiveProjectId: (id: string | null) => void
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
      activeProjectId: null,
      activeSessionId: null,
      loading: false,
      error: null,
      sidebarCollapsed: false,
      fileTreeRefreshToken: 0,

      // ── 查询 ────────────────────────────────────────────────────────────────

      activeWorkspace: () => {
        const { workspaces, activeWorkspaceId } = get()
        return workspaces.find((w) => w.id === activeWorkspaceId) ?? null
      },

      activeProject: () => {
        const { activeProjectId } = get()
        const ws = get().activeWorkspace()
        if (!ws) return null
        if (!activeProjectId) return ws.projects?.[0] ?? null
        return ws.projects?.find((p) => p.id === activeProjectId) ?? null
      },

      activeSessions: () => {
        // 优先返回当前活跃项目的 sessions；无项目时兜底返回工作区扁平 sessions
        const proj = get().activeProject()
        if (proj) return proj.sessions ?? []
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

      // ── 项目操作（三层架构）────────────────────────────────────────────────────

      createProject: async (wsId, name, description = '') => {
        try {
          const proj = await apiCreateProject(wsId, name, description)
          // 乐观插入到对应工作区的 projects 列表
          set((s) => ({
            workspaces: s.workspaces.map((w) =>
              w.id === wsId
                ? { ...w, projects: [{ ...proj, sessions: [] }, ...(w.projects ?? [])] }
                : w
            ),
            activeProjectId: proj.id,
          }))
          return proj
        } catch (e) {
          set({ error: String(e) })
          throw e
        }
      },

      renameProject: async (projId, name) => {
        // 乐观更新
        set((s) => ({
          workspaces: s.workspaces.map((w) => ({
            ...w,
            projects: (w.projects ?? []).map((p) =>
              p.id === projId ? { ...p, name } : p
            ),
          })),
        }))
        try {
          await apiRenameProject(projId, name)
        } catch (e) {
          get().loadWorkspaces()
          set({ error: String(e) })
          throw e
        }
      },

      deleteProject: async (projId) => {
        const prev = get().workspaces
        const { activeProjectId, activeSessionId } = get()
        // 找到被删项目所在的工作区
        const ownerWs = prev.find((w) => w.projects?.some((p) => p.id === projId))
        const deletedProj = ownerWs?.projects?.find((p) => p.id === projId)
        const deletedSessionIds = new Set((deletedProj?.sessions ?? []).map((s) => s.id))
        // 找下一个可用 project
        const nextProj = ownerWs?.projects?.find((p) => p.id !== projId) ?? null
        const nextSession = deletedSessionIds.has(activeSessionId ?? '')
          ? (nextProj?.sessions?.[0] ?? prev.flatMap((w) => w.sessions ?? []).find((s) => !deletedSessionIds.has(s.id)) ?? null)
          : null
        set((s) => ({
          workspaces: s.workspaces.map((w) => ({
            ...w,
            projects: (w.projects ?? []).filter((p) => p.id !== projId),
            sessions: (w.sessions ?? []).filter((s) => !deletedSessionIds.has(s.id)),
          })),
          activeProjectId: activeProjectId === projId ? (nextProj?.id ?? null) : activeProjectId,
          activeSessionId: deletedSessionIds.has(activeSessionId ?? '') ? (nextSession?.id ?? null) : activeSessionId,
          _pendingNavigateSessionId: deletedSessionIds.has(activeSessionId ?? '') ? (nextSession?.id ?? null) : undefined,
        }))
        try {
          await apiDeleteProject(projId)
        } catch (e) {
          set({ workspaces: prev, error: String(e) })
          throw e
        }
      },

      // ── 对话操作 ─────────────────────────────────────────────────────────────

      createSession: async (wsId, title = '新对话', projId?) => {
        try {
          // projId 未指定时，用当前活跃项目（或工作区第一个项目）
          const resolvedProjId = projId ?? get().activeProject()?.id
          const { session_id, workspace_id, project_id } = await apiCreateSession(wsId, title, resolvedProjId)
          const newSession: SessionInfoDto = {
            id: session_id,
            workspace_id: workspace_id ?? wsId,
            project_id: project_id ?? resolvedProjId ?? null,
            title,
            score_title: null,
            score_key: null,
            score_bpm: null,
            score_notes: null,
            pipeline_state: 'idle',
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
          }
          // 乐观插入：优先插入到所属 project.sessions；同时更新扁平 workspace.sessions
          set((s) => ({
            workspaces: s.workspaces.map((w) => {
              if (w.id !== wsId) return w
              return {
                ...w,
                // 更新 project.sessions（三层结构）
                projects: (w.projects ?? []).map((p) =>
                  p.id === (project_id ?? resolvedProjId)
                    ? { ...p, sessions: [newSession, ...(p.sessions ?? [])] }
                    : p
                ),
                // 更新扁平 sessions（向后兼容）
                sessions: [newSession, ...(w.sessions ?? [])],
              }
            }),
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
        const ownerWs = prev.find(
          (w) => w.sessions?.some((s) => s.id === sessionId)
            || w.projects?.some((p) => p.sessions?.some((s) => s.id === sessionId))
        )
        const allSessions = [
          ...(ownerWs?.sessions ?? []),
          ...(ownerWs?.projects ?? []).flatMap((p) => p.sessions ?? []),
        ]
        const nextSession = isActive
          ? allSessions.find((s) => s.id !== sessionId)
            ?? prev.flatMap((w) => [
              ...(w.sessions ?? []),
              ...(w.projects ?? []).flatMap((p) => p.sessions ?? []),
            ]).find((s) => s.id !== sessionId)
            ?? null
          : null
        set((s) => ({
          workspaces: s.workspaces.map((w) => ({
            ...w,
            // 从扁平 sessions 中删除
            sessions: (w.sessions ?? []).filter((sess) => sess.id !== sessionId),
            // 从 project.sessions 中删除（三层结构）
            projects: (w.projects ?? []).map((p) => ({
              ...p,
              sessions: (p.sessions ?? []).filter((sess) => sess.id !== sessionId),
            })),
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
        // 辅助：在工作区列表中查找 session（含三层结构）
        const findInWorkspaces = (wsList: WorkspaceDto[]) => {
          for (const ws of wsList) {
            // 先查 project.sessions（优先，能取到 projId）
            for (const proj of ws.projects ?? []) {
              if (proj.sessions?.some((s) => s.id === sessionId)) {
                return { wsId: ws.id, projId: proj.id }
              }
            }
            // 再查扁平 sessions（兴容旧数据）——此时 projId 未知，用 null 占位
            if (ws.sessions?.some((s) => s.id === sessionId)) {
              return { wsId: ws.id, projId: null }
            }
          }
          return null
        }

        // 先从本地查找（避免重复网络请求）
        const { workspaces } = get()
        const found = findInWorkspaces(workspaces)
        if (found) {
          // 注意：若 projId 为 null（扁平 session），不覆盖已有的 activeProjectId（可能是 URL 传入的）
          const currentProjId = get().activeProjectId
          set({
            activeWorkspaceId: found.wsId,
            activeProjectId: found.projId ?? currentProjId,
            activeSessionId: sessionId,
          })
          return
        }
        // 本地没有（刷新后列表为空）→ 先加载列表，再从列表中查
        try {
          await get().loadWorkspaces()
          const { workspaces: loaded } = get()
          const found2 = findInWorkspaces(loaded)
          if (found2) {
            const currentProjId2 = get().activeProjectId
            set({
              activeWorkspaceId: found2.wsId,
              activeProjectId: found2.projId ?? currentProjId2,
              activeSessionId: sessionId,
            })
            return
          }
          // 列表里也没有，再单独查 session 信息
          const info = await getSessionInfo(sessionId)
          if (info.workspace_id) {
            set({
              activeWorkspaceId: info.workspace_id,
              activeProjectId: info.project_id ?? null,
              activeSessionId: sessionId,
            })
          } else {
            const fallbackWsId = get().workspaces[0]?.id ?? null
            set({ activeWorkspaceId: fallbackWsId, activeProjectId: null, activeSessionId: sessionId })
          }
        } catch {
          const fallbackWsId = get().workspaces[0]?.id ?? null
          set({ activeWorkspaceId: fallbackWsId, activeProjectId: null, activeSessionId: sessionId })
        }
      },

      // ── UI 状态 ──────────────────────────────────────────────────────────────

      setActiveWorkspaceId: (id) => {
        // 切换工作区时同步 activeProjectId 为新工作区第一个项目（避免跨工作区文件错位）
        const ws = get().workspaces.find((w) => w.id === id) ?? null
        const firstProjId = ws?.projects?.[0]?.id ?? null
        set({ activeWorkspaceId: id, activeProjectId: firstProjId })
      },
      setActiveProjectId: (id) => set({ activeProjectId: id }),
      setActiveSessionId: (id) => set({ activeSessionId: id }),
      toggleSidebar: () => set((s) => ({ sidebarCollapsed: !s.sidebarCollapsed })),
      triggerFileTreeRefresh: () => set((s) => ({ fileTreeRefreshToken: s.fileTreeRefreshToken + 1 })),
      clearError: () => set({ error: null }),
      clearPendingNavigate: () => set({ _pendingNavigateSessionId: undefined }),
    }),
    {
      name: 'ep-agent-workspace',
      // 只持久化关键导航状态，不持久化列表数据（每次刷新重新加载）
      partialize: (s) => ({
        activeWorkspaceId: s.activeWorkspaceId,
        activeProjectId: s.activeProjectId,
        activeSessionId: s.activeSessionId,
        sidebarCollapsed: s.sidebarCollapsed,
      }),
    }
  )
)
