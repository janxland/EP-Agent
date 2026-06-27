'use client'

/**
 * /pro/workspace/[wsId] — 工作区主页
 *
 * 展示指定工作区下的所有项目和对话列表。
 * 点击对话跳转到 /pro/[projId]/[sessionId]。
 */

import { useEffect, useMemo, useRef } from 'react'
import { useParams, useRouter } from 'next/navigation'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import Link from 'next/link'

export default function WorkspacePage() {
  const params  = useParams()
  const router  = useRouter()
  const wsId    = params.wsId as string

  const { workspaces, loading: wsLoading, loadWorkspaces, createSession, setActiveWorkspaceId, setActiveProjectId, setActiveSessionId } = useWorkspaceStore()
  const loadedRef = useRef(false)

  // 找到当前工作区
  const workspace = useMemo(() => workspaces.find((w) => w.id === wsId) ?? null, [workspaces, wsId])

  useEffect(() => {
    if (!loadedRef.current) {
      loadedRef.current = true
      loadWorkspaces()
    }
  }, [loadWorkspaces])

  // 激活当前工作区
  useEffect(() => {
    if (wsId) setActiveWorkspaceId(wsId)
  }, [wsId, setActiveWorkspaceId])

  // wsId 无效（加载完成且找不到）→ 跳回 /pro
  useEffect(() => {
    if (!wsLoading && loadedRef.current && !workspace) {
      router.replace('/pro')
    }
  }, [wsLoading, workspace, router])

  const handleSelectSession = (projId: string, sessionId: string) => {
    setActiveWorkspaceId(wsId)
    setActiveProjectId(projId)
    setActiveSessionId(sessionId)
    router.push(`/pro/${projId}/${sessionId}`)
  }

  const handleCreateSession = async (projId: string) => {
    try {
      setActiveWorkspaceId(wsId)
      setActiveProjectId(projId)
      const sess = await createSession(wsId, '新对话', projId)
      router.push(`/pro/${projId}/${sess.id}`)
    } catch (e) {
      console.error('[EP-Agent] 新建对话失败', e)
    }
  }

  if (!workspace) {
    return (
      <div className="flex h-screen items-center justify-center bg-gray-50">
        <div className="flex flex-col items-center gap-3">
          <div className="w-12 h-12 bg-orange-500 rounded-2xl flex items-center justify-center shadow-lg shadow-orange-200 animate-pulse">
            <span className="text-2xl">🎵</span>
          </div>
          <p className="text-sm text-gray-400 font-medium">
            {wsLoading ? '正在加载工作区…' : '工作区不存在，正在跳转…'}
          </p>
        </div>
      </div>
    )
  }

  const projects = workspace.projects ?? []

  return (
    <div className="flex flex-col h-screen bg-gray-50 font-sans">
      {/* 顶栏 */}
      <header className="h-10 bg-white border-b border-gray-100 flex items-center px-4 gap-3 shrink-0 shadow-sm z-10">
        <div className="flex items-center gap-2">
          <div className="w-5 h-5 bg-orange-500 rounded-md flex items-center justify-center shadow-sm shadow-orange-200">
            <span className="text-[10px]">🎵</span>
          </div>
          <span className="font-semibold text-gray-800 text-sm">EP-Agent</span>
        </div>
        <span className="text-gray-200 text-xs">│</span>
        <span className="text-xs text-gray-500 font-medium truncate max-w-[160px]">{workspace.name}</span>
        <div className="ml-auto flex items-center gap-2">
          <Link href="/pro"
            className="text-xs text-gray-400 hover:text-orange-500 transition-colors flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-orange-50">
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            返回
          </Link>
        </div>
      </header>

      {/* 主体 */}
      <div className="flex-1 overflow-y-auto p-6">
        <div className="max-w-3xl mx-auto space-y-6">
          <h1 className="text-lg font-bold text-gray-800">{workspace.name}</h1>

          {projects.length === 0 ? (
            <div className="text-center py-16 text-gray-400 text-sm">暂无项目</div>
          ) : (
            projects.map((proj) => (
              <div key={proj.id} className="bg-white rounded-2xl border border-gray-100 shadow-sm overflow-hidden">
                {/* 项目头 */}
                <div className="flex items-center justify-between px-5 py-3 border-b border-gray-50">
                  <div className="flex items-center gap-2">
                    <span className="w-2 h-2 rounded-full bg-orange-400 shrink-0" />
                    <span className="text-sm font-semibold text-gray-700">{proj.name}</span>
                    <span className="text-[10px] text-gray-300 font-mono">{proj.id.slice(0, 8)}</span>
                  </div>
                  <button
                    onClick={() => void handleCreateSession(proj.id)}
                    className="flex items-center gap-1 text-xs text-orange-400 hover:text-orange-600 hover:bg-orange-50 px-2 py-1 rounded-lg transition-colors"
                  >
                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    新建对话
                  </button>
                </div>

                {/* 对话列表 */}
                <div className="divide-y divide-gray-50">
                  {(proj.sessions ?? []).length === 0 ? (
                    <p className="px-5 py-4 text-xs text-gray-300 text-center">暂无对话，点击「新建对话」开始</p>
                  ) : (
                    (proj.sessions ?? []).map((sess) => (
                      <button
                        key={sess.id}
                        onClick={() => handleSelectSession(proj.id, sess.id)}
                        className="w-full flex items-center gap-3 px-5 py-3 hover:bg-orange-50 transition-colors text-left group"
                      >
                        <span className="w-7 h-7 rounded-xl bg-orange-50 group-hover:bg-orange-100 flex items-center justify-center shrink-0 transition-colors">
                          <span className="text-sm">💬</span>
                        </span>
                        <div className="flex-1 min-w-0">
                          <div className="text-sm font-medium text-gray-700 truncate">{sess.title || '新对话'}</div>
                          <div className="text-[10px] text-gray-300 font-mono">{sess.id.slice(0, 12)}…</div>
                        </div>
                        <svg className="w-3.5 h-3.5 text-gray-200 group-hover:text-orange-300 shrink-0 transition-colors" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
                        </svg>
                      </button>
                    ))
                  )}
                </div>
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  )
}
