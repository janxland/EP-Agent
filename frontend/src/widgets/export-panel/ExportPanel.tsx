'use client'

import { useState } from 'react'
import { useScoreStore } from '@/entities/session/store'
import { exportScore } from '@/shared/lib/api'
import { downloadBlob, formatBPM, formatKey } from '@/shared/lib/utils'
import type { ExportFormat } from '@/shared/types'

const INSTRUMENTS = [
  { value: 0,  label: '🎹 钢琴' },
  { value: 8,  label: '🎶 钢片琴' },
  { value: 11, label: '🎵 音乐盒' },
  { value: 24, label: '🎸 尼龙弦吉他' },
  { value: 40, label: '🎻 小提琴' },
  { value: 46, label: '🪗 竖琴' },
  { value: 73, label: '🎺 长笛' },
  { value: 79, label: '🎶 陶笛' },
]

/**
 * ExportPanel - 导出选项面板
 * 支持导出 ABC / MIDI / JSON 三种格式
 */
export function ExportPanel() {
  const [instrument, setInstrument] = useState(0)
  const [loading, setLoading] = useState<ExportFormat | null>(null)
  const [error, setError] = useState<string | null>(null)

  const { sessionId, score, abcNotation, version, lastEditSummary } = useScoreStore()

  const canExport = !!sessionId && !!score

  const handleExport = async (format: ExportFormat) => {
    if (!canExport || !sessionId || !score) return
    setLoading(format)
    setError(null)
    try {
      const blob = await exportScore(sessionId, format, instrument)
      const title = score.meta.title || 'score'
      const ext = format === 'midi' ? '.mid' : format === 'json' ? '.json' : '.abc'
      downloadBlob(blob, `${title}${ext}`)
    } catch (e) {
      setError(e instanceof Error ? e.message : '导出失败')
    } finally {
      setLoading(null)
    }
  }

  // 直接下载当前 ABC 文本（无需后端）
  const handleDownloadABC = () => {
    if (!abcNotation || !score) return
    const blob = new Blob([abcNotation], { type: 'text/plain;charset=utf-8' })
    downloadBlob(blob, `${score.meta.title || 'score'}.abc`)
  }

  if (!score) {
    return (
      <div className="p-4 text-center text-gray-400 text-sm">
        请先上传谱子
      </div>
    )
  }

  return (
    <div className="p-4 space-y-4">
      {/* 谱子信息摘要 */}
      <div className="bg-gray-50 rounded-lg p-3 space-y-1">
        <p className="text-sm font-medium text-gray-800 truncate">{score.meta.title}</p>
        <div className="flex gap-3 text-xs text-gray-500">
          <span>{formatKey(score.meta.key)}</span>
          <span>{formatBPM(score.meta.bpm)}</span>
          <span>{score.meta.note_count} 音符</span>
          <span className="text-orange-500">v{version}</span>
        </div>
        {lastEditSummary && (
          <p className="text-xs text-orange-500 truncate">最近：{lastEditSummary}</p>
        )}
      </div>

      {/* MIDI 音色选择 */}
      <div>
        <label className="text-xs text-gray-500 mb-1 block">MIDI 音色</label>
        <select
          value={instrument}
          onChange={(e) => setInstrument(Number(e.target.value))}
          className="w-full text-sm border border-gray-200 rounded-lg px-3 py-1.5 focus:outline-none focus:ring-2 focus:ring-orange-300"
        >
          {INSTRUMENTS.map((inst) => (
            <option key={inst.value} value={inst.value}>
              {inst.label}
            </option>
          ))}
        </select>
      </div>

      {/* 导出按钮组 */}
      <div className="space-y-2">
        <button
          onClick={handleDownloadABC}
          className="w-full py-2 px-4 rounded-lg border border-gray-200 text-sm text-gray-700 hover:border-orange-300 hover:text-orange-600 hover:bg-orange-50 transition-all flex items-center justify-center gap-2"
        >
          📄 下载 ABC 谱（本地）
        </button>

        <button
          onClick={() => handleExport('midi')}
          disabled={!!loading}
          className="w-full py-2 px-4 rounded-lg border border-gray-200 text-sm text-gray-700 hover:border-orange-300 hover:text-orange-600 hover:bg-orange-50 transition-all flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {loading === 'midi' ? (
            <span className="w-4 h-4 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
          ) : '🎵'}
          导出 MIDI 文件
        </button>

        <button
          onClick={() => handleExport('json')}
          disabled={!!loading}
          className="w-full py-2 px-4 rounded-lg border border-orange-200 text-sm text-orange-600 bg-orange-50 hover:bg-orange-100 transition-all flex items-center justify-center gap-2 disabled:opacity-50"
        >
          {loading === 'json' ? (
            <span className="w-4 h-4 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
          ) : '📱'}
          导出 Sky JSON（小程序键盘）
        </button>
      </div>

      {error && (
        <p className="text-xs text-red-500 flex items-center gap-1">
          <span>⚠</span> {error}
        </p>
      )}
    </div>
  )
}
