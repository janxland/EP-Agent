'use client'

import {
  useCallback, useEffect, useRef, useState,
  type KeyboardEvent, type ClipboardEvent,
} from 'react'
import { useChatStore } from '@/features/chat/store/chat.store'
import { useScoreStore } from '@/entities/session/store'
import { chatUniversal } from '@/shared/lib/api'
import { ChatMessageList, StreamingAssistantCard } from './ChatMessageList'

// ─── 常量 ─────────────────────────────────────────────────────────────────────

const STICK_SLOP_PX = 80
const REQUEST_TIMEOUT_MS = 60_000

// ─── 附件类型 ─────────────────────────────────────────────────────────────────

type AttachmentKind = 'json' | 'midi' | 'audio' | 'text'

interface Attachment {
  kind: AttachmentKind
  name: string
  content: string   // 文本内容（text/json）或 base64（audio/midi）
  size: number      // 字节数
}

const KIND_ICON: Record<AttachmentKind, string> = {
  json:  '🎮',
  midi:  '🎹',
  audio: '🎵',
  text:  '📄',
}

const KIND_LABEL: Record<AttachmentKind, string> = {
  json:  'Sky JSON',
  midi:  'MIDI',
  audio: '音频',
  text:  '文本',
}

/** 根据文件名/内容判断附件类型 */
function detectKind(name: string, text: string): AttachmentKind {
  const lower = name.toLowerCase()
  if (lower.endsWith('.mid') || lower.endsWith('.midi')) return 'midi'
  if (lower.endsWith('.mp3') || lower.endsWith('.wav') || lower.endsWith('.m4a')) return 'audio'
  if (lower.endsWith('.json')) return 'json'
  // 无扩展名时尝试内容嗅探
  if (text.trimStart().startsWith('[') || text.trimStart().startsWith('{')) {
    try {
      const parsed = JSON.parse(text)
      const arr = Array.isArray(parsed) ? parsed : [parsed]
      if (arr[0]?.songNotes) return 'json'
    } catch { /* ignore */ }
    return 'json'
  }
  return 'text'
}

/** 格式化文件大小 */
function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes}B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`
  return `${(bytes / 1024 / 1024).toFixed(1)}MB`
}

// ─── AttachmentChip ───────────────────────────────────────────────────────────

function AttachmentChip({
  attachment,
  onRemove,
}: {
  attachment: Attachment
  onRemove: () => void
}) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 bg-orange-50 border border-orange-100 rounded-lg text-xs text-orange-700 max-w-[180px]">
      <span>{KIND_ICON[attachment.kind]}</span>
      <span className="truncate flex-1">{attachment.name}</span>
      <span className="text-orange-400 font-mono text-[10px] shrink-0">{fmtSize(attachment.size)}</span>
      <button
        onClick={onRemove}
        className="shrink-0 text-orange-300 hover:text-orange-600 transition-colors ml-0.5"
        aria-label="移除附件"
      >
        ✕
      </button>
    </div>
  )
}

// ─── ChatPanel ────────────────────────────────────────────────────────────────

/**
 * ChatPanel — 专业模式对话面板
 *
 * 架构要点：
 *   ① 统一调用 /chat 接口，LLM 自动识别意图（convert/edit/audio/voice/query）
 *   ② 支持粘贴附件（JSON/文本/MIDI），自动识别类型并作为 attachment 传给后端
 *   ③ 结束信号完全由 SSE 事件驱动（message.completed / abc.updated / error）
 *   ④ 超时兜底：REQUEST_TIMEOUT_MS 后若仍 running 则 failRun
 */
export function ChatPanel() {
  const { sessionId } = useScoreStore()
  const {
    messages,
    streaming,
    status,
    currentStep,
    errorMessage,
    addOptimisticUserMessage,
    startRun,
    failRun,
    resetRuntime,
  } = useChatStore()

  const [input, setInput] = useState('')
  const [attachment, setAttachment] = useState<Attachment | null>(null)

  const scrollRef   = useRef<HTMLDivElement>(null)
  const stickRef    = useRef(true)
  const timeoutRef  = useRef<ReturnType<typeof setTimeout> | null>(null)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // ── 自动滚底 ──────────────────────────────────────────────────────────────
  const onScroll = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    stickRef.current =
      el.scrollHeight - el.scrollTop - el.clientHeight <= STICK_SLOP_PX
  }, [])

  useEffect(() => {
    if (!stickRef.current) return
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [messages, streaming.content, streaming.tool_calls.length])

  // ── 超时兜底清理 ──────────────────────────────────────────────────────────
  useEffect(() => {
    if (status !== 'running') {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
    }
  }, [status])

  // ── 粘贴附件处理 ──────────────────────────────────────────────────────────
  const handlePaste = useCallback(async (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const dt = e.clipboardData as DataTransfer
    const items = Array.from(dt.items) as DataTransferItem[]

    // 1. 优先处理文件粘贴
    const fileItem = items.find(
      (it: DataTransferItem) => it.kind === 'file' && (
        it.type.includes('json') ||
        it.type.includes('midi') ||
        it.type.includes('audio') ||
        it.type.includes('text') ||
        it.type === 'application/octet-stream'
      )
    )
    if (fileItem) {
      e.preventDefault()
      const file = (fileItem as DataTransferItem).getAsFile()
      if (!file) return

      const isAudio = file.type.includes('audio') || /\.(mp3|wav|m4a|ogg|flac)$/i.test(file.name)
      if (isAudio) {
        const reader = new FileReader()
        reader.onload = () => {
          const b64 = (reader.result as string).split(',')[1] ?? ''
          setAttachment({ kind: 'audio', name: file.name, content: b64, size: file.size })
        }
        reader.readAsDataURL(file)
      } else {
        const text = await file.text()
        const kind = detectKind(file.name, text)
        setAttachment({ kind, name: file.name, content: text, size: file.size })
      }
      return
    }

    // 2. 纯文本粘贴：检测是否像 Sky JSON（大段 JSON 作为附件）
    const textItem = items.find(
      (it: DataTransferItem) => it.kind === 'string' && it.type === 'text/plain'
    )
    if (textItem) {
      (textItem as DataTransferItem).getAsString((text: string) => {
        const trimmed = text.trim()
        if (trimmed.length > 200 && (trimmed.startsWith('[') || trimmed.startsWith('{'))) {
          e.preventDefault()
          const kind = detectKind('paste.json', trimmed)
          setAttachment({ kind, name: 'paste.json', content: trimmed, size: trimmed.length })
          setInput((prev) => prev || '帮我加载这首谱子')
        }
      })
    }
  }, [])

  // ── 发送消息 ──────────────────────────────────────────────────────────────
  const handleSend = useCallback(async () => {
    const text = input.trim()
    if (!text || status === 'running' || !sessionId) return

    // 构建用户可见的消息（含附件提示）
    const displayText = attachment
      ? `${text} [附件: ${attachment.name}]`
      : text

    addOptimisticUserMessage(displayText)
    setInput('')
    const att = attachment
    setAttachment(null)
    startRun()

    // 超时兜底
    timeoutRef.current = setTimeout(() => {
      failRun('请求超时，请检查后端连接')
    }, REQUEST_TIMEOUT_MS)

    // 统一调用 /chat 接口，LLM 自动识别意图
    try {
      await chatUniversal(sessionId, {
        message: text,
        attachment_content: att && att.kind !== 'audio' ? att.content : '',
        attachment_name:    att?.name ?? '',
        attachment_b64:     att?.kind === 'audio' ? att.content : '',
      })
      // 结束信号来自 SSE: abc.updated / message.completed / error
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '请求失败，请检查后端服务'
      failRun(msg)
    }
  }, [input, attachment, status, sessionId, addOptimisticUserMessage, startRun, failRun])

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const isRunning        = status === 'running'
  const hasStreamContent = streaming.content || streaming.tool_calls.length > 0 || streaming.reasoning_content

  // 根据当前状态/附件决定 placeholder
  const placeholder = !sessionId
    ? '请先创建 Session...'
    : isRunning
      ? `${currentStep ?? 'AI 处理中'}...`
      : attachment
        ? `描述对「${attachment.name}」的处理意图...`
        : '发消息或粘贴 JSON/音频文件，AI 自动识别意图...'

  return (
    <div className="flex flex-col h-full bg-white">

      {/* ── 顶栏 ── */}
      <div className="flex items-center gap-2 px-4 py-2.5 border-b border-gray-100 shrink-0">
        <div className="flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-orange-400" />
          <span className="text-xs font-semibold text-gray-600">对话</span>
        </div>

        {isRunning && (
          <span className="flex items-center gap-1 text-xs text-orange-500 ml-1">
            <span className="w-2.5 h-2.5 border-2 border-orange-400 border-t-transparent rounded-full animate-spin" />
            {currentStep ?? '处理中'}
          </span>
        )}

        {messages.length > 0 && !isRunning && (
          <button
            onClick={resetRuntime}
            className="ml-auto text-xs text-gray-300 hover:text-gray-500 transition-colors"
            title="清空对话"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
            </svg>
          </button>
        )}
      </div>

      {/* ── 消息列表 ── */}
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 overflow-y-auto px-3 py-4 space-y-3"
      >
        {/* 空状态引导 */}
        {messages.length === 0 && !isRunning && (
          <div className="flex flex-col items-center justify-center h-full text-center py-8 space-y-3">
            <div className="w-14 h-14 bg-gradient-to-br from-orange-50 to-amber-50 rounded-2xl flex items-center justify-center shadow-sm">
              <span className="text-2xl">✨</span>
            </div>
            <div className="space-y-1">
              <p className="text-sm font-semibold text-gray-700">告诉 AI 你想做什么</p>
              <p className="text-xs text-gray-400 max-w-[200px] leading-relaxed">
                直接说话，或粘贴 Sky JSON / 音频文件
              </p>
            </div>
            <div className="flex flex-wrap gap-1.5 justify-center max-w-[240px]">
              {[
                '升高一个八度',
                '加快节奏',
                '生成中国风配乐',
                '克隆我的声音',
                '这首是什么调？',
              ].map((hint) => (
                <button
                  key={hint}
                  onClick={() => setInput(hint)}
                  className="text-xs px-2.5 py-1 bg-gray-50 hover:bg-orange-50 hover:text-orange-500 text-gray-500 rounded-lg transition-colors border border-gray-100 hover:border-orange-200"
                >
                  {hint}
                </button>
              ))}
            </div>
            {/* 粘贴提示 */}
            <p className="text-[10px] text-gray-300 flex items-center gap-1">
              <span>💡</span>
              <span>支持粘贴 Sky JSON / MP3 / MIDI 文件</span>
            </p>
          </div>
        )}

        <ChatMessageList messages={messages} />

        {/* 流式临时消息 */}
        {isRunning && hasStreamContent && (
          <StreamingAssistantCard
            content={streaming.content}
            reasoningContent={streaming.reasoning_content}
            toolCalls={streaming.tool_calls}
          />
        )}

        {/* 仅步骤提示，无流式内容 */}
        {isRunning && !hasStreamContent && currentStep && (
          <div className="flex items-center gap-2 px-3 py-2 rounded-xl bg-orange-50 text-xs text-orange-600">
            <span className="w-3 h-3 border-2 border-orange-400 border-t-transparent rounded-full animate-spin shrink-0" />
            <span>{currentStep}</span>
          </div>
        )}
      </div>

      {/* ── 错误提示 ── */}
      {errorMessage && (
        <div className="mx-3 mb-2 px-3 py-2 bg-red-50 border border-red-100 rounded-xl text-xs text-red-600 flex items-start gap-2">
          <span className="shrink-0 mt-0.5">⚠️</span>
          <span className="flex-1">{errorMessage}</span>
          <button
            onClick={() => useChatStore.getState().resetRuntime()}
            className="shrink-0 text-red-400 hover:text-red-600"
          >
            ✕
          </button>
        </div>
      )}

      {/* ── 输入区 ── */}
      <div className="px-3 pb-3 shrink-0 space-y-1.5">

        {/* 附件 chip */}
        {attachment && (
          <div className="flex items-center gap-2 px-1">
            <AttachmentChip
              attachment={attachment}
              onRemove={() => setAttachment(null)}
            />
            <span className="text-[10px] text-gray-400 flex items-center gap-1">
              <span>{KIND_ICON[attachment.kind]}</span>
              <span>{KIND_LABEL[attachment.kind]} 已就绪，发送时自动识别意图</span>
            </span>
          </div>
        )}

        <div className={[
          'flex items-end gap-2 rounded-xl border p-2 transition-all duration-200',
          isRunning
            ? 'border-gray-100 bg-gray-50 opacity-70'
            : 'border-gray-200 bg-white focus-within:border-orange-300 focus-within:shadow-sm focus-within:shadow-orange-50',
        ].join(' ')}>

          {/* 附件按钮（提示粘贴方式） */}
          <button
            className="shrink-0 w-6 h-6 flex items-center justify-center text-gray-300 hover:text-orange-400 transition-colors"
            title="粘贴 JSON / 音频文件到输入框即可附加"
            onClick={() => textareaRef.current?.focus()}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5}
                d="M15.172 7l-6.586 6.586a2 2 0 102.828 2.828l6.414-6.586a4 4 0 00-5.656-5.656l-6.415 6.585a6 6 0 108.486 8.486L20.5 13" />
            </svg>
          </button>

          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value)
              e.target.style.height = 'auto'
              e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px'
            }}
            onKeyDown={handleKeyDown}
            onPaste={handlePaste}
            disabled={isRunning || !sessionId}
            placeholder={placeholder}
            rows={1}
            style={{ minHeight: '32px', maxHeight: '120px' }}
            className={[
              'flex-1 text-sm resize-none bg-transparent outline-none leading-relaxed py-0.5',
              isRunning || !sessionId ? 'text-gray-300 cursor-not-allowed' : 'text-gray-700 placeholder:text-gray-300',
            ].join(' ')}
          />

          {/* 发送按钮 */}
          <button
            onClick={handleSend}
            disabled={!input.trim() || isRunning || !sessionId}
            className={[
              'shrink-0 w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-150',
              !input.trim() || isRunning || !sessionId
                ? 'bg-gray-100 text-gray-300 cursor-not-allowed'
                : 'bg-orange-500 text-white hover:bg-orange-600 active:scale-90 shadow-sm shadow-orange-200',
            ].join(' ')}
          >
            {isRunning ? (
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

        <p className="text-[10px] text-gray-300 text-right pr-0.5">
          Enter 发送 · Shift+Enter 换行 · 粘贴文件自动识别
        </p>
      </div>
    </div>
  )
}
