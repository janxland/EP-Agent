'use client'

/**
 * /pro — 专业模式入口守卫页
 *
 * 职责：
 *   1. 若 localStorage 中有 activeSessionId → 直接跳转到 /pro/{sessionId}（恢复上次）
 *   2. 否则加载工作区列表，创建新 session 后跳转
 *
 * 真正的 UI 渲染在 /pro/[sessionId]/page.tsx 中，此页面不渲染任何业务 UI。
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
      routerRef.current.replace(`/pro/${_pendingNavigateSessionId}`)
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
        .then((res) => {
          if (res.ok) {
            router.replace(`/pro/${persistedSessionId}`)
          } else {
            // session 已失效（404 或其他错误）→ 清除持久化 ID，走步骤 2 创建新 session
            setActiveSessionId(null)
            initializedRef.current = false  // 允许步骤 2 继续执行
            loadWorkspaces()
          }
        })
        .catch(() => {
          // 网络错误时乐观跳转（后端可能只是暂时不可用）
          router.replace(`/pro/${persistedSessionId}`)
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
        const sess = await wsCreateSession(wsId, '新对话')
        setActiveSessionId(sess.id)
        router.replace(`/pro/${sess.id}`)
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
          const { session_id } = await apiCreateSession(fallbackWsId, '新对话')
          setActiveSessionId(session_id)
          router.replace(`/pro/${session_id}`)
        } catch (e2) {
          console.error('[EP-Agent] 降级创建 session 也失败', e2)
          // 最终兜底：直接创建无工作区 session
          try {
            const { session_id } = await apiCreateSession(undefined, '新对话')
            setActiveSessionId(session_id)
            router.replace(`/pro/${session_id}`)
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
