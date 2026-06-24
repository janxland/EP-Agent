'use client'

/**
 * @deprecated IntentPanel 已被 ChatPanel 完全覆盖，功能重复。
 *
 * 原设计为「小白模式」独立意图输入面板，现已由 ChatPanel 统一承接：
 *   - 快捷意图预设 → ChatPanel 顶部快捷操作栏
 *   - 意图输入框   → ChatPanel 底部消息输入框
 *   - AI 路由逻辑  → 统一走 /chat 接口，由 LLM 自动识别意图
 *
 * 此组件暂时保留以防回退，后续版本将移除。
 * 请勿在新功能中引用此组件。
 */

import { useCallback, useRef, useState } from 'react'
import { useScoreStore } from '@/entities/session/store'
import { chatUniversal } from '@/shared/lib/api'

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
 * IntentPanel - 意图输入面板（小白模式）
 * 统一调用 /chat 接口，LLM 自动识别意图（edit/query/audio...）
 * 不再直接调用 /edit，避免 "no score in session" 500 错误
 */
export function IntentPanel() {
  const [intent, setIntent] = useState('')
  const [isLoading, setIsLoading] = useState(false)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const {
    sessionId,
    score,
    pipelineState,
    setPipelineState,
    appendLog,
    clearLogs,
  } = useScoreStore()

  const canSubmit =
    !!sessionId &&
    intent.trim().length > 0 &&
    !isLoading &&
    pipelineState !== 'running'

  const handleSubmit = useCallback(async () => {
    if (!canSubmit || !sessionId) return

    const trimmedIntent = intent.trim()
    setIsLoading(true)
    clearLogs()
    setPipelineState('running')
    appendLog({ type: 'activity', text: `用户意图：${trimmedIntent}` })

    try {
      // 统一走 /chat 接口，LLM 自动路由意图
      // 无谱子时 LLM 会友好提示，不会 500
      await chatUniversal(sessionId, { message: trimmedIntent })
      setIntent('')
      // pipelineState 由 SSE pipeline.step 事件驱动更新
    } catch (e) {
      const msg = e instanceof Error ? e.message : '请求失败'
      appendLog({ type: 'error', text: msg, status: 'failed' })
      setPipelineState('failed')
    } finally {
      setIsLoading(false)
    }
  }, [canSubmit, sessionId, intent, clearLogs, setPipelineState, appendLog])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
      handleSubmit()
    }
  }

  const applyPreset = (value: string) => {
    setIntent(value)
    inputRef.current?.focus()
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
          placeholder={
            score
              ? '描述修改意图，如：转调到 A 大调、改成爵士风格...'
              : '描述你想要的音乐，如：写一段抒情流行 ABC...'
          }
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
            AI 处理中...
          </>
        ) : (
          <>✨ {score ? '智能修改' : 'AI 创作'}</>
        )}
      </button>

      {/* 无谱子时的提示 */}
      {!score && (
        <p className="text-[11px] text-gray-400 text-center leading-relaxed">
          💡 没有谱子也能用！AI 会直接创作 ABC 谱子，<br />
          或者先在上方上传 Sky JSON 文件
        </p>
      )}
    </div>
  )
}
