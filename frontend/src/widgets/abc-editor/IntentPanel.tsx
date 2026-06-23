'use client'

import { useCallback, useRef, useState } from 'react'
import { useScoreStore } from '@/entities/session/store'
import { editABC } from '@/shared/lib/api'

const INTENT_PRESETS = [
  { label: '转 G 大调', value: '转调到 G 大调' },
  { label: '转 F 大调', value: '转调到 F 大调' },
  { label: '升高半音', value: '升高 1 个半音' },
  { label: '降低半音', value: '降低 1 个半音' },
  { label: 'BPM +20', value: '加快 20%' },
  { label: 'BPM -20', value: '放慢 20%' },
  { label: '爵士风格', value: '改成爵士风格' },
  { label: '中国风', value: '改成中国风格' },
]

/**
 * IntentPanel - 意图输入面板
 * 用户在此输入修改意图，触发 Agent 编排
 */
export function IntentPanel() {
  const [intent, setIntent] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  // SSE 订阅由 page.tsx 统一管理，IntentPanel 只调用 REST
  const {
    sessionId,
    score,
    pipelineState,
    updateABC,
    setPipelineState,
    appendLog,
    clearLogs,
  } = useScoreStore()

  const canSubmit = !!sessionId && !!score && intent.trim().length > 0 && !isLoading && pipelineState !== 'running'

  const handleSubmit = useCallback(async () => {
    if (!canSubmit || !sessionId) return

    const trimmedIntent = intent.trim()
    setIsLoading(true)
    clearLogs()
    setPipelineState('running')
    appendLog({ type: 'activity', text: `用户意图：${trimmedIntent}` })

    try {
      // SSE 已由 page.tsx 持久订阅，事件会自动流入 store，无需在此订阅
      const result = await editABC(sessionId, trimmedIntent)
      updateABC(result.abc_notation, result.version, result.summary)
      appendLog({
        type: 'step',
        text: `✓ ${result.summary}`,
        status: 'succeeded',
      })
      setIntent('')
    } catch (e) {
      const msg = e instanceof Error ? e.message : '修改失败'
      appendLog({ type: 'error', text: msg, status: 'failed' })
      setPipelineState('failed')
    } finally {
      setIsLoading(false)
    }
  }, [canSubmit, sessionId, intent, clearLogs, setPipelineState, appendLog, updateABC])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleSubmit()
    }
  }

  const applyPreset = (value: string) => {
    setIntent(value)
    inputRef.current?.focus()
  }

  if (!score) {
    return (
      <div className="p-4 text-center text-gray-400 text-sm">
        请先上传谱子
      </div>
    )
  }

  return (
    <div className="p-4 space-y-3">
      {/* 快捷意图按钮 */}
      <div className="flex flex-wrap gap-1.5">
        {INTENT_PRESETS.map((p) => (
          <button
            key={p.value}
            onClick={() => applyPreset(p.value)}
            disabled={isLoading}
            className="px-2.5 py-1 text-xs rounded-full border border-gray-200 text-gray-600 hover:border-orange-300 hover:text-orange-600 hover:bg-orange-50 transition-all disabled:opacity-40"
          >
            {p.label}
          </button>
        ))}
      </div>

      {/* 意图输入框 */}
      <div className="relative">
        <textarea
          ref={inputRef}
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="描述你想要的修改，如：转调到 A 大调、改成爵士风格、加快节奏..."
          rows={3}
          disabled={isLoading}
          className="w-full px-3 py-2.5 text-sm rounded-lg border border-gray-200 resize-none focus:outline-none focus:ring-2 focus:ring-orange-300 focus:border-transparent disabled:opacity-60 placeholder:text-gray-300"
        />
        <span className="absolute bottom-2 right-2 text-xs text-gray-300">
          ⌘↵ 发送
        </span>
      </div>

      {/* 提交按钮 */}
      <button
        onClick={handleSubmit}
        disabled={!canSubmit}
        className="w-full py-2 rounded-lg text-sm font-medium transition-all bg-orange-500 text-white hover:bg-orange-600 disabled:opacity-40 disabled:cursor-not-allowed flex items-center justify-center gap-2"
      >
        {isLoading ? (
          <>
            <span className="w-4 h-4 border-2 border-white border-t-transparent rounded-full animate-spin" />
            AI 正在修改...
          </>
        ) : (
          <>✨ 智能修改</>
        )}
      </button>
    </div>
  )
}
