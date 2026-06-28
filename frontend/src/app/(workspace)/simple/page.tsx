'use client'

import { useEffect, useRef, useState } from 'react'
import { UploadPanel } from '@/widgets/upload-panel/UploadPanel'
import { ABCRenderer } from '@/widgets/abc-editor/ABCRenderer'
import { IntentPanel } from '@/widgets/abc-editor/IntentPanel'
import { PipelineStatus } from '@/widgets/pipeline-status/PipelineStatus'
import { ExportPanel } from '@/widgets/export-panel/ExportPanel'
import { AudioPanel } from '@/widgets/audio-panel/AudioPanel'
import { useScoreStore } from '@/entities/session/store'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'
import { subscribeToSession } from '@/shared/lib/api'
import Link from 'next/link'

export default function SimplePage() {
  const { sessionId, setSessionId, abcNotation, score, handleSSEEvent } = useScoreStore()
  const { activeWorkspaceId, activeProjectId, activeProject, createSession } = useWorkspaceStore()
  const resolvedProjectId = activeProjectId ?? activeProject()?.id ?? undefined
  const [rightTab, setRightTab] = useState<'export' | 'audio'>('export')
  const unsubRef = useRef<(() => void) | null>(null)

  useEffect(() => {
    if (!sessionId) {
      // 使用 store.createSession 确保 session 写入 workspaces 树，侧边栏可见
      // project_id 必须传递，工具层通过它定位文件系统隔离路径
      createSession(activeWorkspaceId ?? '', undefined, resolvedProjectId)
        .then((sess) => setSessionId(sess.id))
        .catch(console.error)
      return
    }
    unsubRef.current?.()
    unsubRef.current = subscribeToSession(sessionId, handleSSEEvent)
    return () => { unsubRef.current?.(); unsubRef.current = null }
  }, [sessionId, activeWorkspaceId, setSessionId, handleSSEEvent])

  return (
    <div className="flex flex-col h-screen bg-gray-50">
      {/* ── 顶栏 ── */}
      <header className="h-12 bg-white border-b border-gray-100 flex items-center px-6 gap-3 shrink-0">
        <span className="text-lg">🎵</span>
        <span className="font-semibold text-gray-800 text-sm">EP-Agent</span>
        <span className="text-gray-300 text-xs">|</span>
        <span className="text-xs text-gray-400">Sky 谱子智能编辑器</span>
        {score && (
          <>
            <span className="text-gray-300 text-xs ml-2">|</span>
            <span className="text-xs text-orange-500 font-medium">{score.meta.title}</span>
            <span className="text-xs text-gray-400">
              {score.meta.key} · ♩={Math.round(score.meta.bpm)} · {score.meta.note_count} 音符
            </span>
          </>
        )}
        <div className="ml-auto">
          <Link
            href="/pro"
            className="text-xs text-gray-400 hover:text-orange-500 transition-colors flex items-center gap-1"
          >
            切换专业模式 →
          </Link>
        </div>
      </header>

      {/* ── 主体三栏 ── */}
      <div className="flex flex-1 overflow-hidden">

        {/* ── 左侧面板 ── */}
        <aside className="w-72 bg-white border-r border-gray-100 flex flex-col overflow-hidden shrink-0">
          <div className="border-b border-gray-100">
            <div className="px-4 pt-4 pb-2">
              <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">上传谱子</h2>
            </div>
            <UploadPanel />
          </div>
          <div className="border-b border-gray-100">
            <div className="px-4 pt-3 pb-2">
              <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">智能修改</h2>
            </div>
            <IntentPanel />
          </div>
          <div className="flex-1 overflow-hidden flex flex-col">
            <div className="px-4 pt-3 pb-2 shrink-0">
              <h2 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">执行日志</h2>
            </div>
            <div className="flex-1 overflow-y-auto">
              <PipelineStatus />
            </div>
          </div>
        </aside>

        {/* ── 中央乐谱区 ── */}
        <main className="flex-1 overflow-y-auto bg-white">
          {abcNotation ? (
            <div className="p-6">
              <details className="mb-4 group">
                <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none flex items-center gap-1">
                  <span className="group-open:rotate-90 transition-transform inline-block">▶</span>
                  ABC 源码
                </summary>
                <pre className="mt-2 p-3 bg-gray-50 rounded-lg text-xs text-gray-600 overflow-x-auto whitespace-pre-wrap font-mono border border-gray-100">
                  {abcNotation}
                </pre>
              </details>
              <div className="border border-gray-100 rounded-xl overflow-hidden">
                <ABCRenderer abc={abcNotation} title={score?.meta.title} />
              </div>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-full text-center p-8">
              <div className="text-6xl mb-4">🎼</div>
              <h3 className="text-lg font-medium text-gray-700 mb-2">上传 Sky 谱子开始编辑</h3>
              <p className="text-sm text-gray-400 max-w-sm leading-relaxed">
                支持 Sky: Children of the Light 游戏导出的 JSON 格式谱子。
                上传后可以用 AI 进行转调、变速、风格转换，最终导出 ABC / MIDI / JSON。
              </p>
              <div className="mt-6 grid grid-cols-3 gap-3 text-xs text-gray-400">
                <div className="bg-gray-50 rounded-lg p-3 text-center"><div className="text-xl mb-1">📤</div><div>上传 JSON</div></div>
                <div className="bg-gray-50 rounded-lg p-3 text-center"><div className="text-xl mb-1">✨</div><div>AI 编辑</div></div>
                <div className="bg-gray-50 rounded-lg p-3 text-center"><div className="text-xl mb-1">💾</div><div>导出文件</div></div>
              </div>
            </div>
          )}
        </main>

        {/* ── 右侧面板 ── */}
        <aside className="w-72 bg-white border-l border-gray-100 flex flex-col overflow-hidden shrink-0">
          <div className="flex border-b border-gray-100 shrink-0">
            {(['export', 'audio'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setRightTab(tab)}
                className={`flex-1 py-3 text-xs font-medium transition-colors ${
                  rightTab === tab
                    ? 'border-b-2 border-orange-500 text-orange-500'
                    : 'text-gray-400 hover:text-gray-600'
                }`}
              >
                {tab === 'export' ? '导出' : '🎵 音频生成'}
              </button>
            ))}
          </div>
          <div className={`flex-1 overflow-y-auto ${rightTab === 'export' ? 'block' : 'hidden'}`}>
            <ExportPanel />
          </div>
          <div className={`flex-1 overflow-y-auto ${rightTab === 'audio' ? 'block' : 'hidden'}`}>
            <AudioPanel />
          </div>
        </aside>
      </div>
    </div>
  )
}
