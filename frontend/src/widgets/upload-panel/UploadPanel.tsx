'use client'

import { useCallback, useState } from 'react'
import { useScoreStore } from '@/entities/session/store'
import { createSession, convertJSON } from '@/shared/lib/api'

/**
 * UploadPanel - JSON 谱子上传区域
 * 支持拖拽上传和点击上传
 * 上传后自动创建 Session 并触发转换
 */
export function UploadPanel() {
  const [isDragging, setIsDragging] = useState(false)
  const [isLoading, setIsLoading] = useState(false)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  // SSE 订阅由 page.tsx 统一管理，UploadPanel 不再自建 EventSource
  const { setSessionId, setScore, setPipelineState, appendLog, reset, sessionId } = useScoreStore()

  const handleFile = useCallback(
    async (file: File) => {
      if (!file.name.endsWith('.json') && !file.name.endsWith('.txt')) {
        setErrorMsg('请上传 .json 或 .txt 格式的 Sky 谱子文件')
        return
      }

      setIsLoading(true)
      setErrorMsg(null)
      setPipelineState('running')
      appendLog({ type: 'activity', text: `正在读取文件：${file.name}` })

      try {
        const content = await file.text()

        // 验证 JSON 格式
        let parsed: unknown
        try {
          parsed = JSON.parse(content)
        } catch {
          throw new Error('文件不是有效的 JSON 格式')
        }

        // 检查 Sky JSON 特征
        const arr = Array.isArray(parsed) ? parsed : [parsed]
        const first = arr[0] as Record<string, unknown>
        if (!first?.songNotes) {
          throw new Error('不是有效的 Sky/CUBY 谱子格式（缺少 songNotes 字段）')
        }

        // 每次上传新文件时重置 store，避免旧谱子状态污染新文件
        reset()

        // 创建新 Session（每次上传都强制新建，避免旧 session 历史污染）
        const { session_id } = await createSession()
        const sid = session_id
        setSessionId(sid)  // 这会触发 page.tsx 的 useEffect 重新建立 SSE

        // 触发转换（SSE 订阅已由 page.tsx 建立，事件会自动流入 store）
        const result = await convertJSON(sid, content, file.name)

        setScore({
          id: result.score_id,
          title: result.meta.title,
          abc_notation: result.abc_notation,
          meta: result.meta,
          version: 1,
        })

        appendLog({
          type: 'step',
          text: `✓ 转换完成：${result.meta.title}，共 ${result.meta.note_count} 个音符`,
          status: 'succeeded',
        })
      } catch (e) {
        const msg = e instanceof Error ? e.message : '转换失败'
        setErrorMsg(msg)
        setPipelineState('failed')
        appendLog({ type: 'error', text: msg, status: 'failed' })
      } finally {
        setIsLoading(false)
      }
    },
    [sessionId, setSessionId, setScore, setPipelineState, appendLog, reset]
  )

  const onDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault()
      setIsDragging(false)
      const file = e.dataTransfer.files[0]
      if (file) handleFile(file)
    },
    [handleFile]
  )

  const onInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0]
      if (file) handleFile(file)
      e.target.value = '' // 允许重复上传同一文件
    },
    [handleFile]
  )

  return (
    <div className="p-4">
      <label
        className={[
          'flex flex-col items-center justify-center w-full h-36 rounded-xl border-2 border-dashed cursor-pointer transition-all',
          isDragging
            ? 'border-orange-400 bg-orange-50'
            : 'border-gray-200 bg-gray-50 hover:border-orange-300 hover:bg-orange-50/50',
          isLoading ? 'opacity-60 pointer-events-none' : '',
        ].join(' ')}
        onDragOver={(e) => { e.preventDefault(); setIsDragging(true) }}
        onDragLeave={() => setIsDragging(false)}
        onDrop={onDrop}
      >
        <input
          type="file"
          accept=".json,.txt"
          className="hidden"
          onChange={onInputChange}
          disabled={isLoading}
        />
        {isLoading ? (
          <div className="flex flex-col items-center gap-2 text-orange-500">
            <div className="w-6 h-6 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
            <span className="text-sm">正在转换...</span>
          </div>
        ) : (
          <div className="flex flex-col items-center gap-2 text-gray-400">
            <span className="text-3xl">🎼</span>
            <span className="text-sm font-medium text-gray-600">
              拖拽或点击上传 Sky JSON 谱子
            </span>
            <span className="text-xs text-gray-400">.json / .txt 格式</span>
          </div>
        )}
      </label>

      {errorMsg && (
        <p className="mt-2 text-xs text-red-500 flex items-center gap-1">
          <span>⚠</span> {errorMsg}
        </p>
      )}
    </div>
  )
}
