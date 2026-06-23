'use client'

import { useState, useRef, useEffect, useCallback, KeyboardEvent } from 'react'

// ─── 类型定义 ─────────────────────────────────────────────────────────────────

export interface AudioAttachment {
  type: 'audio'
  b64: string       // data:audio/xxx;base64,... 格式
  filename: string
  durationLabel?: string  // 预览用，如 "10s"
}

// ─── 快捷建议词 ───────────────────────────────────────────────────────────────

const QUICK_SUGGESTIONS = [
  '再欢快一点',
  '换成爵士风',
  '加入中国风乐器',
  '去掉人声，纯音乐',
  '节奏慢一点，更抒情',
  '换成电子风格',
  '加入钢琴',
  '更有力量感',
]

// ─── Props ────────────────────────────────────────────────────────────────────

interface Props {
  disabled?: boolean
  isGenerating?: boolean
  placeholder?: string
  suggestions?: string[]
  /** 是否允许附加音频文件（用于音色克隆场景） */
  allowAttachment?: boolean
  onSend: (message: string, attachment?: AudioAttachment) => void
}

// ─── 工具函数 ─────────────────────────────────────────────────────────────────

/** 将 File 读取为 base64 DataURL（异步，非阻塞） */
function readFileAsBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(reader.result as string)
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}

// ─── 组件 ─────────────────────────────────────────────────────────────────────

/**
 * AudioChatInput - 对话式音频迭代输入框
 *
 * 职责（单一）：
 *   1. 文本输入与快捷建议词
 *   2. 可选音频文件附件（base64 读取，不关心业务逻辑）
 *   3. 通过 onSend(message, attachment?) 向上透传，不持有业务状态
 */
export function AudioChatInput({
  disabled = false,
  isGenerating = false,
  placeholder,
  suggestions = [],
  allowAttachment = false,
  onSend,
}: Props) {
  const [message, setMessage] = useState('')
  const [attachment, setAttachment] = useState<AudioAttachment | null>(null)
  const [isReadingFile, setIsReadingFile] = useState(false)

  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // 自动聚焦
  useEffect(() => {
    if (!disabled && !isGenerating) {
      textareaRef.current?.focus()
    }
  }, [disabled, isGenerating])

  // ── 发送 ─────────────────────────────────────────────────────────────────────
  const handleSend = useCallback(() => {
    const text = message.trim()
    if (!text || disabled || isGenerating || isReadingFile) return
    onSend(text, attachment ?? undefined)
    setMessage('')
    setAttachment(null)
  }, [message, disabled, isGenerating, isReadingFile, attachment, onSend])

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleQuickSuggestion = (s: string) => {
    if (disabled || isGenerating) return
    onSend(s)
  }

  // ── 文件附件 ──────────────────────────────────────────────────────────────────
  const handleFileChange = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (!file) return

    // 重置 input，允许重复选同一文件
    e.target.value = ''

    setIsReadingFile(true)
    try {
      const b64 = await readFileAsBase64(file)
      setAttachment({ type: 'audio', b64, filename: file.name })
    } catch {
      // 读取失败静默清除
      setAttachment(null)
    } finally {
      setIsReadingFile(false)
    }
  }, [])

  const handleRemoveAttachment = useCallback(() => {
    setAttachment(null)
    if (fileInputRef.current) fileInputRef.current.value = ''
  }, [])

  // ── 合并建议词 ────────────────────────────────────────────────────────────────
  const displaySuggestions = [
    ...suggestions,
    ...QUICK_SUGGESTIONS.filter((s) => !suggestions.includes(s)),
  ].slice(0, 6)

  const isBlocked = disabled || isGenerating || isReadingFile

  return (
    <div className="space-y-2">
      {/* 隐藏文件输入 */}
      {allowAttachment && (
        <input
          ref={fileInputRef}
          type="file"
          accept="audio/*"
          className="hidden"
          onChange={handleFileChange}
        />
      )}

      {/* 快捷建议标签 */}
      {displaySuggestions.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {displaySuggestions.map((s) => (
            <button
              key={s}
              onClick={() => handleQuickSuggestion(s)}
              disabled={isBlocked}
              className={[
                'text-xs px-2.5 py-1 rounded-full border transition-all',
                isBlocked
                  ? 'border-gray-100 text-gray-300 cursor-not-allowed'
                  : 'border-orange-200 text-orange-500 hover:bg-orange-50 hover:border-orange-300 active:scale-95',
              ].join(' ')}
            >
              {s}
            </button>
          ))}
        </div>
      )}

      {/* 附件预览条 */}
      {attachment && (
        <div className="flex items-center gap-2 px-2.5 py-1.5 bg-purple-50 border border-purple-200 rounded-lg">
          <svg className="w-3.5 h-3.5 text-purple-400 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
              d="M15.536 8.464a5 5 0 010 7.072M12 18.364V5.636M8.464 8.464a5 5 0 000 7.072" />
          </svg>
          <span className="text-xs text-purple-600 flex-1 truncate">{attachment.filename}</span>
          <button
            onClick={handleRemoveAttachment}
            className="text-purple-300 hover:text-purple-500 transition-colors"
            title="移除附件"
          >
            <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>
      )}

      {/* 输入区域 */}
      <div className={[
        'flex items-end gap-2 rounded-xl border p-2 transition-all',
        isBlocked
          ? 'border-gray-100 bg-gray-50'
          : 'border-orange-200 bg-white focus-within:border-orange-400 focus-within:shadow-sm focus-within:shadow-orange-100',
      ].join(' ')}>

        {/* 附件按钮 */}
        {allowAttachment && (
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isBlocked}
            title="附加音频文件（用于音色克隆）"
            className={[
              'shrink-0 w-7 h-7 rounded-lg flex items-center justify-center transition-all',
              isBlocked
                ? 'text-gray-200 cursor-not-allowed'
                : attachment
                  ? 'text-purple-400 bg-purple-50 hover:bg-purple-100'
                  : 'text-gray-300 hover:text-orange-400 hover:bg-orange-50',
            ].join(' ')}
          >
            {isReadingFile ? (
              <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
              </svg>
            ) : (
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                  d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
              </svg>
            )}
          </button>
        )}

        <textarea
          ref={textareaRef}
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={isBlocked}
          placeholder={
            placeholder ??
            (isGenerating
              ? '生成中，请稍候...'
              : isReadingFile
                ? '正在读取音频文件...'
                : '告诉 AI 如何改进，如"再欢快一点"、"换成爵士风"...')
          }
          rows={2}
          className={[
            'flex-1 text-xs resize-none bg-transparent outline-none leading-relaxed',
            isBlocked ? 'text-gray-300 cursor-not-allowed' : 'text-gray-700',
          ].join(' ')}
        />

        {/* 发送按钮 */}
        <button
          onClick={handleSend}
          disabled={!message.trim() || isBlocked}
          className={[
            'shrink-0 w-8 h-8 rounded-lg flex items-center justify-center transition-all',
            !message.trim() || isBlocked
              ? 'bg-gray-100 text-gray-300 cursor-not-allowed'
              : 'bg-orange-500 text-white hover:bg-orange-600 active:scale-90 shadow-sm',
          ].join(' ')}
          title="发送（Enter）"
        >
          {isGenerating ? (
            <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
            </svg>
          ) : (
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 12h14M12 5l7 7-7 7" />
            </svg>
          )}
        </button>
      </div>

      {/* 提示文字 */}
      <p className="text-xs text-gray-300 text-right">
        Enter 发送 · Shift+Enter 换行{allowAttachment ? ' · 📎 附加音频克隆音色' : ''}
      </p>
    </div>
  )
}
