'use client'

/**
 * /audio — 音频生成控制台（一级路由）
 *
 * 与 /pro、/simple 同级，专注于：
 *   - AI 对话式配乐
 *   - 音色克隆
 *   - 多服务商（MiniMax / Suno AI）管理
 *
 * 不依赖乐谱编辑工作区，可独立访问。
 */

import { useEffect, useRef, useState } from 'react'
import { AudioPanel } from '@/widgets/audio-panel/AudioPanel'
import { useScoreStore } from '@/entities/session/store'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import { subscribeToSession } from '@/shared/lib/api'
import Link from 'next/link'

export default function AudioConsolePage() {
  const { sessionId, setSessionId, handleSSEEvent } = useScoreStore()
  const { activeWorkspaceId, activeProjectId, activeProject, createSession } = useWorkspaceStore()
  const resolvedProjectId = activeProjectId ?? activeProject()?.id ?? undefined
  const unsubRef = useRef<(() => void) | null>(null)
  const [ready, setReady] = useState(false)

  // ── Session 初始化：复用 simple 模式的逻辑 ──────────────────────────────────
  useEffect(() => {
    if (!sessionId) {
      createSession(activeWorkspaceId ?? '', undefined, resolvedProjectId)
        .then((sess) => {
          setSessionId(sess.id)
          setReady(true)
        })
        .catch(console.error)
      return
    }
    setReady(true)
    unsubRef.current?.()
    unsubRef.current = subscribeToSession(sessionId, handleSSEEvent)
    return () => { unsubRef.current?.(); unsubRef.current = null }
  }, [sessionId, activeWorkspaceId, setSessionId, handleSSEEvent])

  return (
    <div className="flex flex-col h-screen bg-gray-50">

      {/* ── 顶栏 ── */}
      <header className="h-12 bg-white border-b border-gray-100 flex items-center px-6 gap-3 shrink-0 shadow-sm shadow-gray-50 z-10">
        <div className="flex items-center gap-2.5">
          <div className="w-7 h-7 bg-orange-500 rounded-xl flex items-center justify-center shadow-sm shadow-orange-200">
            <span className="text-sm">🎵</span>
          </div>
          <span className="font-semibold text-gray-800 text-sm">EP-Agent</span>
        </div>

        <span className="text-gray-200 text-xs">│</span>
        <span className="text-xs text-gray-500 font-medium">音频生成控制台</span>

        {/* session 状态指示 */}
        <div className="flex items-center gap-1.5 ml-2">
          <span className={['w-1.5 h-1.5 rounded-full transition-colors', ready ? 'bg-green-400' : 'bg-gray-300 animate-pulse'].join(' ')} />
          <span className="text-[10px] text-gray-400 font-mono">
            {ready ? (sessionId ? sessionId.slice(0, 8) + '…' : '就绪') : '初始化…'}
          </span>
        </div>

        <nav className="ml-auto flex items-center gap-1">
          <Link
            href="/simple"
            className="text-xs text-gray-400 hover:text-orange-500 transition-colors px-2.5 py-1.5 rounded-lg hover:bg-orange-50 flex items-center gap-1"
          >
            小白模式
          </Link>
          <Link
            href="/pro"
            className="text-xs text-gray-400 hover:text-orange-500 transition-colors px-2.5 py-1.5 rounded-lg hover:bg-orange-50 flex items-center gap-1"
          >
            专业模式
          </Link>
          <Link
            href="/"
            className="text-xs text-gray-400 hover:text-orange-500 transition-colors px-2.5 py-1.5 rounded-lg hover:bg-orange-50 flex items-center gap-1"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 19l-7-7m0 0l7-7m-7 7h18" />
            </svg>
            返回
          </Link>
        </nav>
      </header>

      {/* ── 主体：左侧说明 + 右侧控制台 ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── 左侧：功能说明 ── */}
        <aside className="hidden lg:flex w-72 xl:w-80 bg-white border-r border-gray-100 flex-col p-6 gap-6 overflow-y-auto shrink-0">

          {/* 功能介绍 */}
          <div className="space-y-3">
            <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">功能说明</h2>
            <div className="space-y-2.5">
              {[
                { icon: '🎼', title: 'AI 对话式配乐', desc: '用自然语言描述风格，AI 自动生成背景音乐' },
                { icon: '🎙', title: '音色克隆', desc: '上传 10s 音频，克隆你的声音用于合成' },
                { icon: '🔄', title: '迭代优化', desc: '对生成结果持续对话调整，直到满意为止' },
                { icon: '🎧', title: '多服务商', desc: 'MiniMax、Suno AI 自动路由，按需切换' },
              ].map((item) => (
                <div key={item.title} className="flex gap-3 p-3 rounded-xl bg-gray-50 hover:bg-orange-50/50 transition-colors">
                  <span className="text-xl shrink-0 mt-0.5">{item.icon}</span>
                  <div className="space-y-0.5 min-w-0">
                    <p className="text-xs font-medium text-gray-700">{item.title}</p>
                    <p className="text-xs text-gray-400 leading-relaxed">{item.desc}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* 快速上手 */}
          <div className="space-y-3">
            <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">快速上手</h2>
            <ol className="space-y-2">
              {[
                '选择服务商（自动 / MiniMax / Suno）',
                '点击风格预设快速生成，或直接描述',
                '对结果说"再欢快一点"持续迭代',
                '音色克隆：附加音频 → 发送「克隆我的声音」',
              ].map((step, i) => (
                <li key={i} className="flex items-start gap-2.5 text-xs text-gray-500">
                  <span className="shrink-0 w-4 h-4 rounded-full bg-orange-100 text-orange-500 font-semibold flex items-center justify-center text-[10px]">
                    {i + 1}
                  </span>
                  <span className="leading-relaxed">{step}</span>
                </li>
              ))}
            </ol>
          </div>

          {/* API Key 配置 */}
          <div className="space-y-3">
            <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">API Key 配置</h2>
            <div className="space-y-2 text-xs text-gray-400 leading-relaxed">
              <p>MiniMax: <code className="bg-gray-100 px-1.5 py-0.5 rounded text-gray-600">MINIMAX_API_KEY</code></p>
              <p>Suno: <code className="bg-gray-100 px-1.5 py-0.5 rounded text-gray-600">SUNO_API_KEY</code>（via TTAPI）</p>
              <div className="flex gap-3 pt-1">
                <a href="https://platform.minimax.io" target="_blank" rel="noopener noreferrer"
                  className="text-orange-400 hover:underline">MiniMax →</a>
                <a href="https://ttapi.io" target="_blank" rel="noopener noreferrer"
                  className="text-orange-400 hover:underline">TTAPI →</a>
              </div>
            </div>
          </div>
        </aside>

        {/* ── 中央：音频生成控制台 ── */}
        <main className="flex-1 flex flex-col overflow-hidden bg-white min-w-0">

          {/* 控制台标题栏 */}
          <div className="flex items-center gap-2 px-5 py-3 border-b border-gray-100 shrink-0">
            <div className="w-6 h-6 bg-orange-50 rounded-lg flex items-center justify-center">
              <svg className="w-3.5 h-3.5 text-orange-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
              </svg>
            </div>
            <span className="text-sm font-medium text-gray-700">音频生成控制台</span>
            <span className="text-xs text-gray-400 ml-1">· AI 对话式配乐 &amp; 音色克隆</span>
          </div>

          {/* AudioPanel 全高展示 */}
          <div className="flex-1 overflow-y-auto">
            {ready ? (
              <div className="max-w-2xl mx-auto py-2">
                <AudioPanel />
              </div>
            ) : (
              <div className="flex flex-col items-center justify-center h-full gap-3">
                <div className="w-10 h-10 bg-orange-500 rounded-2xl flex items-center justify-center shadow-lg shadow-orange-200 animate-pulse">
                  <span className="text-xl">🎵</span>
                </div>
                <p className="text-sm text-gray-400">正在初始化会话…</p>
              </div>
            )}
          </div>
        </main>

        {/* ── 右侧：占位（可扩展为历史记录/收藏等） ── */}
        <aside className="hidden xl:flex w-64 bg-white border-l border-gray-100 flex-col p-5 gap-4 overflow-y-auto shrink-0">
          <div className="space-y-3">
            <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">其他模式</h2>
            <div className="space-y-2">
              <Link href="/simple" className="group flex items-center gap-3 p-3 rounded-xl border border-gray-100 hover:border-orange-200 hover:bg-orange-50/50 transition-all">
                <span className="text-xl">🌟</span>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-gray-700 group-hover:text-orange-600">小白模式</p>
                  <p className="text-[10px] text-gray-400 truncate">乐谱编辑 + 导出</p>
                </div>
              </Link>
              <Link href="/pro" className="group flex items-center gap-3 p-3 rounded-xl border border-gray-100 hover:border-orange-200 hover:bg-orange-50/50 transition-all">
                <span className="text-xl">⚡</span>
                <div className="min-w-0">
                  <p className="text-xs font-medium text-gray-700 group-hover:text-orange-600">专业模式</p>
                  <p className="text-[10px] text-gray-400 truncate">IDE 布局 · 全功能</p>
                </div>
              </Link>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
