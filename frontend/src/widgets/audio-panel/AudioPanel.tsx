'use client'

import { useState, useCallback, useEffect, useRef } from 'react'
import type { AudioTurn, AudioProvider } from '@/shared/types'
import { chatAudioStream, clearAudioHistory, getAudioHistory } from '@/shared/lib/api'
import type { AudioStreamCallbacks } from '@/shared/lib/api'
import { useScoreStore } from '@/entities/session/store'
import { AudioHistoryList } from './AudioHistoryList'
import { AudioChatInput } from './AudioChatInput'
import type { AudioAttachment } from './AudioChatInput'

// ─── 常量 ─────────────────────────────────────────────────────────────────────

const STYLE_PRESETS = [
  { label: '中国风', prompt: '给这首谱子配乐，中国风纯音乐，古筝二胡' },
  { label: '流行',   prompt: '给这首谱子配乐，现代流行风格' },
  { label: '爵士',   prompt: '给这首谱子配乐，爵士风格，夜晚氛围' },
  { label: '古典',   prompt: '给这首谱子配乐，古典交响乐风格' },
  { label: '电子',   prompt: '给这首谱子配乐，电子合成器风格' },
  { label: '民谣',   prompt: '给这首谱子配乐，民谣吉他风格，温暖' },
] as const

const PROVIDER_LABELS: Record<string, string> = {
  auto: '自动',
  minimax: 'MiniMax',
  suno: 'Suno AI',
}

// ─── 流式进度步骤 ─────────────────────────────────────────────────────────────

interface StreamStep {
  id: string
  text: string
  status: 'running' | 'succeeded' | 'failed' | ''
}

// ─── 工具函数 ─────────────────────────────────────────────────────────────────

function extractTitle(abc: string): string | null {
  return abc.match(/^T:\s*(.+)$/m)?.[1]?.trim() ?? null
}

function isVoiceCloneIntent(message: string): boolean {
  return /克隆|音色|声音|voice.*clone|clone.*voice/i.test(message)
}

// ─── 组件 ─────────────────────────────────────────────────────────────────────

export function AudioPanel() {
  const { sessionId, abcNotation } = useScoreStore()

  const [history, setHistory] = useState<AudioTurn[]>([])
  const [currentTurn, setCurrentTurn] = useState<number | null>(null)
  const [isGenerating, setIsGenerating] = useState(false)
  const [error, setError] = useState('')
  const [provider, setProvider] = useState<'auto' | 'minimax' | 'suno'>('auto')

  // 流式进度步骤列表
  const [streamSteps, setStreamSteps] = useState<StreamStep[]>([])

  // 用于取消流式请求
  const abortCtrlRef = useRef<AbortController | null>(null)

  // ── 恢复历史 ─────────────────────────────────────────────────────────────────
  useEffect(() => {
    if (!sessionId) return
    getAudioHistory(sessionId)
      .then(({ history: turns }) => {
        if (turns.length > 0) {
          setHistory(turns)
          setCurrentTurn(turns[turns.length - 1].turn)
        }
      })
      .catch(() => {})
  }, [sessionId])

  const lastTurn = history[history.length - 1] ?? null
  const lastSuggestions = lastTurn?.suggestions ?? []
  const isFirstTime = history.length === 0

  // ── 更新/追加步骤 ─────────────────────────────────────────────────────────────
  const upsertStep = useCallback((id: string, text: string, status: StreamStep['status']) => {
    setStreamSteps((prev) => {
      const idx = prev.findIndex((s) => s.id === id)
      if (idx >= 0) {
        const next = [...prev]
        next[idx] = { id, text, status }
        return next
      }
      return [...prev, { id, text, status }]
    })
  }, [])

  // ── 核心发送逻辑（流式版）────────────────────────────────────────────────────
  const handleSend = useCallback(async (message: string, attachment?: AudioAttachment) => {
    if (!sessionId) {
      setError('请先创建或加载一个会话')
      return
    }

    // 取消上一次未完成的请求
    abortCtrlRef.current?.abort()
    abortCtrlRef.current = null

    setIsGenerating(true)
    setError('')
    setStreamSteps([])

    const audioB64 = attachment?.b64 ?? ''

    const callbacks: AudioStreamCallbacks = {
      onStep({ step, status, text }) {
        upsertStep(step, text, status as StreamStep['status'])
      },
      onToolCall({ call_id, tool, status, result_preview }) {
        const label =
          status === 'running'   ? `调用工具：${tool}` :
          status === 'succeeded' ? `${tool} 完成${result_preview ? `：${result_preview.slice(0, 40)}` : ''}` :
                                   `${tool} 失败`
        upsertStep(
          `tool_${call_id}`,
          label,
          status === 'running' ? 'running' : status === 'succeeded' ? 'succeeded' : 'failed',
        )
      },
      onResult(result) {
        const newTurn: AudioTurn = {
          ...result,
          // 优先用后端权威 turn 值，回退到本地计数（防止 result.turn 为 0 或未定义）
          turn: result.turn || history.length + 1,
          user_message: result.user_message || message,
        }
        setHistory((prev) => [...prev, newTurn])
        setCurrentTurn(newTurn.turn)
        setIsGenerating(false)
        setStreamSteps([])
      },
      onError(errMsg) {
        const msg = errMsg ?? '生成失败，请检查 API Key 配置'
        setError(
          msg.includes('API_KEY') || msg.includes('未配置')
            ? 'API Key 未配置，请在后端设置 MINIMAX_API_KEY 或 SUNO_API_KEY 环境变量'
            : msg,
        )
        setIsGenerating(false)
        setStreamSteps([])
      },
    }

    const ac = chatAudioStream(sessionId, message, provider, audioB64, callbacks)
    abortCtrlRef.current = ac
  }, [sessionId, provider, history.length, upsertStep])

  // 组件卸载时取消请求
  useEffect(() => {
    return () => { abortCtrlRef.current?.abort() }
  }, [])

  // ── 清空历史 ──────────────────────────────────────────────────────────────────
  const handleClearHistory = useCallback(async () => {
    if (sessionId) {
      try { await clearAudioHistory(sessionId) } catch {}
    }
    setHistory([])
    setCurrentTurn(null)
    setError('')
  }, [sessionId])

  // ── 点击历史轮次折叠/展开 ─────────────────────────────────────────────────────
  const handlePlayTurn = useCallback((turn: AudioTurn) => {
    setCurrentTurn((prev) => (prev === turn.turn ? null : turn.turn))
  }, [])

  // ── 首次生成：从 ABC 谱自动构造 prompt ───────────────────────────────────────
  const handleFirstGenerate = useCallback((stylePrompt: string) => {
    if (!abcNotation) { handleSend(stylePrompt); return }
    const title = extractTitle(abcNotation)
    const fullPrompt = title
      ? `给《${title}》这首谱子配乐，${stylePrompt.replace('给这首谱子配乐，', '')}`
      : stylePrompt
    handleSend(fullPrompt)
  }, [abcNotation, handleSend])

  // ── 生成中文案 ────────────────────────────────────────────────────────────────
  const isVoiceClone    = isVoiceCloneIntent(history[history.length - 1]?.user_message ?? '')
  const generatingLabel = isVoiceClone ? '正在克隆音色...' : isFirstTime ? 'AI 正在生成音乐...' : 'AI 正在处理...'
  const generatingEta   = isVoiceClone ? '约 10-30 秒' : provider === 'suno' ? '约 1-3 分钟' : '约 30-60 秒'

  // 取最近 5 条步骤展示
  const visibleSteps = streamSteps.slice(-5)

  return (
    <div className="p-4 space-y-4">

      {/* ── Header：服务商选择 ── */}
      <div className="flex items-center gap-2">
        <span className="text-xs text-gray-400 shrink-0">服务商</span>
        <div className="flex gap-1 flex-1">
          {(['auto', 'minimax', 'suno'] as const).map((p) => (
            <button
              key={p}
              onClick={() => setProvider(p)}
              className={[
                'flex-1 py-1 rounded-lg text-xs font-medium transition-all',
                provider === p
                  ? 'bg-orange-500 text-white shadow-sm'
                  : 'bg-gray-100 text-gray-500 hover:bg-gray-200',
              ].join(' ')}
            >
              {PROVIDER_LABELS[p]}
            </button>
          ))}
        </div>
      </div>

      {/* ── 首次生成：风格预设快捷选择 ── */}
      {isFirstTime && (
        <div className="space-y-3">
          <div className="text-center py-6 space-y-2">
            <div className="w-12 h-12 bg-orange-50 rounded-full flex items-center justify-center mx-auto">
              <svg className="w-6 h-6 text-orange-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                  d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3" />
              </svg>
            </div>
            <p className="text-sm font-medium text-gray-700">AI 对话式配乐 · 音色克隆</p>
            <p className="text-xs text-gray-400 leading-relaxed">
              选择风格快速生成，或附上音频克隆你的声音
            </p>
          </div>

          <div className="grid grid-cols-3 gap-2">
            {STYLE_PRESETS.map((preset) => (
              <button
                key={preset.label}
                onClick={() => handleFirstGenerate(preset.prompt)}
                disabled={isGenerating}
                className="py-2.5 rounded-xl border border-gray-200 text-xs text-gray-600
                  hover:border-orange-300 hover:text-orange-500 hover:bg-orange-50
                  active:scale-95 transition-all disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {preset.label}
              </button>
            ))}
          </div>

          {/* 音色克隆入口提示 */}
          <div className="flex items-center gap-2 px-3 py-2 bg-purple-50 border border-purple-100 rounded-xl">
            <span className="text-base">🎙</span>
            <p className="text-xs text-purple-500 leading-relaxed">
              想克隆你的声音？点击输入框的 <strong>📎</strong> 按钮附加 10s 音频，然后发送「克隆我的声音」
            </p>
          </div>
        </div>
      )}

      {/* ── 对话历史列表 ── */}
      {history.length > 0 && (
        <AudioHistoryList
          history={history}
          currentTurn={currentTurn}
          onPlayTurn={handlePlayTurn}
          onClearHistory={handleClearHistory}
          sessionId={sessionId ?? undefined}
        />
      )}

      {/* ── 生成中：流式实时步骤进度 ── */}
      {isGenerating && (
        <div className="bg-orange-50 border border-orange-100 rounded-xl px-4 py-3 space-y-2">
          {/* 标题行 */}
          <div className="flex items-center gap-2">
            <span className="w-4 h-4 border-2 border-orange-400 border-t-transparent rounded-full animate-spin shrink-0" />
            <p className="text-xs font-medium text-orange-600">{generatingLabel}</p>
            <span className="text-xs text-orange-400 ml-auto">{generatingEta}</span>
          </div>

          {/* 流式步骤列表 */}
          {visibleSteps.length > 0 && (
            <div className="space-y-1 pl-6">
              {visibleSteps.map((step) => (
                <div key={step.id} className="flex items-start gap-1.5 text-xs">
                  {step.status === 'running' && (
                    <span className="w-2 h-2 mt-0.5 border border-orange-400 border-t-transparent rounded-full animate-spin shrink-0" />
                  )}
                  {step.status === 'succeeded' && (
                    <span className="text-green-500 shrink-0 mt-0.5">✓</span>
                  )}
                  {step.status === 'failed' && (
                    <span className="text-red-400 shrink-0 mt-0.5">✗</span>
                  )}
                  {step.status === '' && (
                    <span className="text-orange-300 shrink-0 mt-0.5">·</span>
                  )}
                  <span className={
                    step.status === 'running'   ? 'text-orange-500' :
                    step.status === 'failed'    ? 'text-red-400'    :
                    step.status === 'succeeded' ? 'text-green-600'  :
                    'text-orange-400'
                  }>
                    {step.text}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── 错误提示 ── */}
      {error && (
        <div className="bg-red-50 border border-red-100 rounded-xl px-3 py-2.5 text-xs text-red-600 leading-relaxed">
          ⚠️ {error}
        </div>
      )}

      {/* ── 对话输入框 ── */}
      <AudioChatInput
        disabled={!sessionId}
        isGenerating={isGenerating}
        allowAttachment
        placeholder={
          !sessionId
            ? '请先上传谱子文件...'
            : isFirstTime
              ? '描述音乐风格，或附加音频克隆声音...'
              : '告诉 AI 如何改进，如"再欢快一点"、"换成爵士风"...'
        }
        suggestions={lastSuggestions}
        onSend={handleSend}
      />

      {/* ── 配置提示（折叠） ── */}
      <details className="group">
        <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-500 list-none flex items-center gap-1">
          <svg className="w-3 h-3 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          API Key 配置说明
        </summary>
        <div className="mt-2 text-xs text-gray-400 leading-relaxed space-y-1 pl-4">
          <p>MiniMax: <code className="bg-gray-100 px-1 rounded">MINIMAX_API_KEY</code></p>
          <p>Suno: <code className="bg-gray-100 px-1 rounded">SUNO_API_KEY</code>（via TTAPI）</p>
          <div className="flex gap-3 mt-1">
            <a href="https://platform.minimax.io" target="_blank" rel="noopener noreferrer"
              className="text-orange-400 hover:underline">MiniMax →</a>
            <a href="https://ttapi.io" target="_blank" rel="noopener noreferrer"
              className="text-orange-400 hover:underline">TTAPI →</a>
          </div>
        </div>
      </details>
    </div>
  )
}
