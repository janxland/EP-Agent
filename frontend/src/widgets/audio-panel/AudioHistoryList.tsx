'use client'

import { useRef, useState, useCallback } from 'react'
import type { AudioTurn } from '@/shared/types'
import { AudioDomain } from '@/shared/types'

// ─── VoiceCloneCard ───────────────────────────────────────────────────────────
// 高内聚：所有 voice_clone 域的渲染逻辑封装于此，与 AudioMusicCard 完全解耦

interface VoiceCloneCardProps {
  turn: AudioTurn
  isActive: boolean
}

function VoiceCloneCard({ turn, isActive }: VoiceCloneCardProps) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(() => {
    if (!turn.voice_id) return
    navigator.clipboard.writeText(turn.voice_id).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }, [turn.voice_id])

  return (
    <div className="space-y-2.5">
      {/* 域标签 */}
      <div className="flex items-center gap-1.5">
        <span className="text-xs px-2 py-0.5 rounded-full bg-purple-50 text-purple-500 font-medium">
          🎙 音色克隆
        </span>
        {turn.voice_id && (
          <span className="text-xs px-2 py-0.5 rounded-full bg-green-50 text-green-500">
            已克隆
          </span>
        )}
      </div>

      {/* voice_id 展示 + 复制 */}
      {turn.voice_id ? (
        <div className="flex items-center gap-2 bg-gray-50 border border-gray-200 rounded-lg px-3 py-2">
          <code className="flex-1 text-xs text-gray-700 font-mono truncate">
            {turn.voice_id}
          </code>
          <button
            onClick={handleCopy}
            title="复制 voice_id"
            className={[
              'shrink-0 text-xs px-2 py-0.5 rounded transition-all',
              copied
                ? 'bg-green-100 text-green-600'
                : 'bg-white border border-gray-200 text-gray-400 hover:text-purple-500 hover:border-purple-300',
            ].join(' ')}
          >
            {copied ? '已复制' : '复制'}
          </button>
        </div>
      ) : (
        <p className="text-xs text-gray-400 italic">voice_id 获取中...</p>
      )}

      {/* demo_audio 试听播放器 */}
      {isActive && turn.demo_audio && (
        <div className="space-y-1">
          <p className="text-xs text-gray-400">试听样本</p>
          <audio
            src={turn.demo_audio}
            controls
            autoPlay={false}
            className="w-full"
            style={{ height: '32px' }}
            onClick={(e) => e.stopPropagation()}
          />
        </div>
      )}

      {/* 使用建议 */}
      {turn.voice_id && (
        <p className="text-xs text-gray-400 leading-relaxed bg-blue-50 rounded-lg px-2.5 py-1.5">
          💡 可在「音色合成」中使用此 voice_id 将任意文字转为你的声音
        </p>
      )}
    </div>
  )
}

// ─── AudioMusicCard ───────────────────────────────────────────────────────────
// 原有音乐生成 / 迭代 / 翻唱域的渲染逻辑，内聚封装

interface AudioMusicCardProps {
  turn: AudioTurn
  isActive: boolean
}

function AudioMusicCard({ turn, isActive }: AudioMusicCardProps) {
  const isDiff = turn.domain === AudioDomain.ITERATE && !!turn.diff_summary
  const durationSec = turn.duration_ms ? Math.round(turn.duration_ms / 1000) : null

  return (
    <div className="space-y-2">
      {/* 时长 + 服务商 */}
      <div className="flex items-center gap-1.5 justify-end">
        {durationSec && (
          <span className="text-xs text-gray-400">{durationSec}s</span>
        )}
        <span className={[
          'text-xs px-1.5 py-0.5 rounded-full',
          turn.provider === 'minimax'
            ? 'bg-blue-50 text-blue-500'
            : 'bg-purple-50 text-purple-500',
        ].join(' ')}>
          {turn.provider || 'auto'}
        </span>
      </div>

      {/* Diff 摘要（迭代时显示改了什么） */}
      {isDiff && (
        <div className="text-xs text-orange-600 bg-orange-50 rounded-lg px-2 py-1 leading-relaxed">
          🔄 {turn.diff_summary}
        </div>
      )}

      {/* 生成摘要 */}
      {turn.summary && (
        <p className="text-xs text-gray-500 leading-relaxed line-clamp-2">
          {turn.summary}
        </p>
      )}

      {/* 播放器（当前轮次展开） */}
      {isActive && turn.audio_url && (
        <audio
          src={turn.audio_url}
          controls
          autoPlay={false}
          className="w-full"
          style={{ height: '32px' }}
          onClick={(e) => e.stopPropagation()}
        />
      )}

      {/* 建议标签 */}
      {isActive && turn.suggestions && turn.suggestions.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {turn.suggestions.map((s, i) => (
            <span
              key={i}
              className="text-xs px-2 py-0.5 bg-white border border-gray-200 rounded-full text-gray-500"
            >
              {s}
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── 域路由：按 AudioDomain 枚举分发渲染，O(1) switch，零字符串比较 ────────────

function TurnCardBody({ turn, isActive }: { turn: AudioTurn; isActive: boolean }) {
  switch (turn.domain) {
    case AudioDomain.CLONE:
      return <VoiceCloneCard turn={turn} isActive={isActive} />
    case AudioDomain.GENERATE:
    case AudioDomain.ITERATE:
    case AudioDomain.COVER:
    default:
      return <AudioMusicCard turn={turn} isActive={isActive} />
  }
}

// ─── AudioHistoryList ─────────────────────────────────────────────────────────

interface Props {
  history: AudioTurn[]
  currentTurn: number | null
  onPlayTurn: (turn: AudioTurn) => void
  onClearHistory: () => void
}

/**
 * AudioHistoryList - 音频对话历史列表
 *
 * 职责（单一）：列表骨架 + 轮次标头 + 域路由（委托给 TurnCardBody）
 * 不内联任何域渲染逻辑，保持低耦合
 */
export function AudioHistoryList({ history, currentTurn, onPlayTurn, onClearHistory }: Props) {
  const listRef = useRef<HTMLDivElement>(null)

  if (history.length === 0) return null

  return (
    <div className="space-y-1.5">
      {/* 标题栏 */}
      <div className="flex items-center justify-between">
        <span className="text-xs font-medium text-gray-500">
          对话历史 · {history.length} 轮
        </span>
        <button
          onClick={onClearHistory}
          className="text-xs text-gray-400 hover:text-red-400 transition-colors"
          title="清空历史，重新开始"
        >
          清空
        </button>
      </div>

      {/* 历史列表 */}
      <div ref={listRef} className="space-y-2 max-h-72 overflow-y-auto pr-1">
        {history.map((turn) => {
          const isActive = currentTurn === turn.turn

          return (
            <div
              key={turn.turn}
              className={[
                'rounded-xl border p-3 space-y-2 transition-all cursor-pointer',
                isActive
                  ? 'border-orange-300 bg-orange-50/80 shadow-sm'
                  : 'border-gray-100 bg-gray-50/60 hover:border-gray-200',
              ].join(' ')}
              onClick={() => onPlayTurn(turn)}
            >
              {/* 轮次标头（所有域通用） */}
              <div className="flex items-center gap-1.5 min-w-0">
                <span className={[
                  'shrink-0 w-5 h-5 rounded-full text-xs flex items-center justify-center font-medium',
                  isActive ? 'bg-orange-500 text-white' : 'bg-gray-200 text-gray-500',
                ].join(' ')}>
                  {turn.turn}
                </span>
                <span className="text-xs text-gray-700 truncate">
                  {turn.user_message}
                </span>
              </div>

              {/* 域内容（委托路由） */}
              <TurnCardBody turn={turn} isActive={isActive} />
            </div>
          )
        })}
      </div>
    </div>
  )
}
