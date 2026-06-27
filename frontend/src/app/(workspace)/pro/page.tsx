'use client'

/**
 * /pro — 专业模式入口守卫页
 *
 * 职责：
 *   1. 若 localStorage 中有 activeSessionId → 直接跳转到 /pro/{sessionId}（恢复上次）
 *   2. 否则加载工作区列表，创建新 session 后跳转
 *
 * 真正的 UI 渲染在 /pro/[projId]/[sessionId]/page.tsx 中，此页面不渲染任何业务 UI。
 */

import { useEffect, useRef } from 'react'
import { useRouter } from 'next/navigation'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import { createSession as apiCreateSession } from '@/shared/lib/api'

export default function ProPage() {
  const router = useRouter()
  const {
    activeSessionId: persistedSessionId,
    activeWorkspaceId,
    loadWorkspaces,
    createSession: wsCreateSession,
    setActiveSessionId,
    _pendingNavigateSessionId,
    clearPendingNavigate,
  } = useWorkspaceStore()

  const creatingRef     = useRef(false)
  const initializedRef  = useRef(false)

  // ── 用 ref 稳定 router，避免 router 对象变化引发多余 re-run ─────────────────
  const routerRef = useRef(router)
  useEffect(() => { routerRef.current = router })

  // ── 监听删除后的跳转信号（从 [sessionId] 页面删除后退到此页再跳转）──────────
  useEffect(() => {
    if (_pendingNavigateSessionId === undefined) return
    clearPendingNavigate()
    if (_pendingNavigateSessionId) {
      // 从 workspaces 树中查找目标 session 所属 projId，避免 activeProject() 为空
      const state = useWorkspaceStore.getState()
      const pendingProjId = state.workspaces
        .flatMap((w) => w.projects ?? [])
        .find((p) => p.sessions?.some((s) => s.id === _pendingNavigateSessionId))?.id
        ?? state.activeProjectId
        ?? ''
      if (!pendingProjId) {
        // projId 实在找不到，回到 /pro 重新走守卫逻辑
        routerRef.current.replace('/pro')
        return
      }
      routerRef.current.replace(`/pro/${pendingProjId}/${_pendingNavigateSessionId}`)
    } else {
      routerRef.current.replace('/pro')
    }
  }, [_pendingNavigateSessionId, clearPendingNavigate])

  // ── 步骤 1：优先恢复上次活跃的 session（含有效性验证）──────────────────────────
  useEffect(() => {
    if (initializedRef.current) return
    if (persistedSessionId) {
      initializedRef.current = true
      // 验证 session 是否仍然有效（后端可能已删除）
      fetch(`/api/sessions/${persistedSessionId}`)
        .then(async (res) => {
          if (res.ok) {
            // 额外检查 stale 字段：后端内存中无此 session 时返回 stale:true（HTTP 200）
            // stale session 仍可跳转，后端发消息时会自动重建（含 workspace_id/project_id 恢复）
            // 但若 stale 且后端 DB 中也无此 session，则清除并重建
            let restoredProjId = ''
            try {
              const data = await res.json()
              if (data?.stale && !data?.workspace_id) {
                // DB 中也找不到工作区归属，此 session 已完全失效，重建
                setActiveSessionId(null)
                initializedRef.current = false
                loadWorkspaces()
                return
              }
              // 优先用接口返回的 project_id（fix22 已修复该字段）
              if (data?.project_id) {
                restoredProjId = data.project_id
              } else {
                // 接口没有 project_id，从已加载的 workspaces 树中查找
                const state = useWorkspaceStore.getState()
                restoredProjId = state.workspaces
                  .flatMap((w) => w.projects ?? [])
                  .find((p) => p.sessions?.some((s) => s.id === persistedSessionId))?.id
                  ?? state.activeProjectId
                  ?? ''
              }
            } catch {
              // json 解析失败：从 store 取
              const state = useWorkspaceStore.getState()
              restoredProjId = state.activeProjectId ?? ''
            }
            if (!restoredProjId) {
              // projId 仍为空：先加载工作区列表再重试
              await loadWorkspaces()
              const state = useWorkspaceStore.getState()
              restoredProjId = state.workspaces
                .flatMap((w) => w.projects ?? [])
                .find((p) => p.sessions?.some((s) => s.id === persistedSessionId))?.id
                ?? state.workspaces.flatMap((w) => w.projects ?? [])[0]?.id
                ?? ''
            }
            router.replace(`/pro/${restoredProjId}/${persistedSessionId}`)
          } else {
            // session 已失效（404 或其他错误）→ 清除持久化 ID，走步骤 2 创建新 session
            setActiveSessionId(null)
            initializedRef.current = false  // 允许步骤 2 继续执行
            loadWorkspaces()
          }
        })
        .catch(() => {
          // 网络错误时乐观跳转（后端可能只是暂时不可用）
          const fallbackProjId = useWorkspaceStore.getState().activeProject()?.id ?? ''
          router.replace(`/pro/${fallbackProjId}/${persistedSessionId}`)
        })
    } else {
      // 无历史 session，触发工作区加载，为步骤 2 准备
      loadWorkspaces()
    }
  }, [persistedSessionId, loadWorkspaces, router, setActiveSessionId])

  // ── 步骤 2：工作区加载完成后创建新 session ────────────────────────────────────
  const { workspaces, loading: wsLoading } = useWorkspaceStore()
  useEffect(() => {
    if (persistedSessionId || initializedRef.current || wsLoading || creatingRef.current) return
    // wsLoading=false 说明加载已完成（包括后端返回空数组的情况），可以继续创建
    // 注意：不能在这里用 workspaces.length === 0 提前 return，否则全新用户永远卡死

    creatingRef.current  = true
    initializedRef.current = true

    const doCreate = async () => {
      try {
        let wsId = activeWorkspaceId ?? workspaces[0]?.id
        if (!wsId) {
          // 工作区为空（全新用户）→ 先创建默认工作区
          const newWs = await useWorkspaceStore.getState().createWorkspace('默认工作区')
          wsId = newWs.id
        }
        // 传入 activeProjectId（若有），确保 session 归属正确项目，工具层文件隔离生效
        const resolvedProjId = useWorkspaceStore.getState().activeProject()?.id
        const sess = await wsCreateSession(wsId, '新对话', resolvedProjId)
        setActiveSessionId(sess.id)
        // createSession 返回的 session 含 project_id（store 已乐观插入），优先使用
        const newProjId = sess.project_id
          ?? resolvedProjId
          ?? useWorkspaceStore.getState().activeProject()?.id
          ?? useWorkspaceStore.getState().workspaces.find((w) => w.id === wsId)?.projects?.[0]?.id
          ?? ''
        router.replace(`/pro/${newProjId}/${sess.id}`)
      } catch (e) {
        console.error('[EP-Agent] 创建 session 失败', e)
        // 降级：直接调用 apiCreateSession，不依赖工作区
        try {
          const state = useWorkspaceStore.getState()
          let fallbackWsId = state.workspaces[0]?.id
          if (!fallbackWsId) {
            // 工作区列表仍为空，再创建一次
            const newWs = await state.createWorkspace('默认工作区')
            fallbackWsId = newWs.id
          }
          // 获取降级工作区下的项目 ID（确保 session 有文件隔离边界）
          const fallbackState2 = useWorkspaceStore.getState()
          const fallbackProjId2 = fallbackState2.workspaces
            .find((w) => w.id === fallbackWsId)?.projects?.[0]?.id
          const { session_id } = await apiCreateSession(fallbackWsId, '新对话', fallbackProjId2)
          setActiveSessionId(session_id)
          router.replace(`/pro/${fallbackProjId2 ?? ''}/${session_id}`)
        } catch (e2) {
          console.error('[EP-Agent] 降级创建 session 也失败', e2)
          // 最终兜底：必须有工作区，否则工具层无法定位文件系统路径
          // 此时工作区列表为空，再创建一次工作区再建 session
          try {
            const emergencyWs = await useWorkspaceStore.getState().createWorkspace('默认工作区')
            // 新工作区刚创建，需等 store 更新后读取自动生成的默认项目 ID
            const emergencyProjId = useWorkspaceStore.getState()
              .workspaces.find((w) => w.id === emergencyWs.id)?.projects?.[0]?.id
            const { session_id } = await apiCreateSession(emergencyWs.id, '新对话', emergencyProjId)
            setActiveSessionId(session_id)
            router.replace(`/pro/${emergencyProjId ?? ''}/${session_id}`)
          } catch (e3) {
            console.error('[EP-Agent] 最终兜底也失败', e3)
          }
        }
      } finally {
        creatingRef.current = false
      }
    }
    doCreate()
  }, [persistedSessionId, workspaces, wsLoading, activeWorkspaceId, wsCreateSession, setActiveSessionId, router])

  // ── 加载占位符 ────────────────────────────────────────────────────────────────
  return (
    <div className="flex h-screen items-center justify-center bg-gray-50">
      <div className="flex flex-col items-center gap-3">
        <div className="w-12 h-12 bg-orange-500 rounded-2xl flex items-center justify-center shadow-lg shadow-orange-200 animate-pulse">
          <span className="text-2xl">🎵</span>
        </div>
        <p className="text-sm text-gray-400 font-medium">正在加载工作区…</p>
        <p className="text-xs text-gray-300">首次访问将自动创建默认工作区</p>
      </div>
    </div>
  )
}
