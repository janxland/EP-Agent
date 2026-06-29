'use client'

/**
 * ChatMessageList — 消息列表渲染
 *
 * 优化点：
 * 1. 流式 Markdown 渲染（标题/代码块/列表/粗体/斜体/链接）
 * 2. 思考过程（reasoning）实时流式展开，有打字机效果
 * 3. 工具卡片与 assistant 消息严格绑定，不孤立漂浮
 * 4. 用户气泡支持 [@文件名] chip，保持橙色风格
 * 5. 流式光标精细化（只在最后一个字符后显示）
 */

import React, { Fragment, memo, useMemo, useState, useEffect, useRef, useCallback } from 'react'
import type { ReactNode } from 'react'

import type {
  ChatMessage,
  ChatAssistantMessage,
  ChatToolMessage,
} from '@/features/chat/types/chat.types'
import { ToolCard } from './tool-call/ToolCard'
import { FileChip } from './RichInput'
import { useWorkspaceStore } from '@/features/workspace/store/workspace.store'

// ─── Markdown 渲染器（轻量级，无依赖）─────────────────────────────────────────

/**
 * 将 Markdown 文本渲染为 React 节点树。
 * 支持：标题(#)、代码块(```)、行内代码(`)、粗体(**)、斜体(*)、
 *       无序列表(-)、有序列表(1.)、链接([text](url))、[@文件名] chip
 */
/** 过滤 LLM 偶发输出的 XML tool_call 标签残片（如 `}</tool_call>`） */
function stripToolCallTags(raw: string): string {
  // 完整块：<tool_call>...</tool_call>（含跨行）
  let s = raw.replace(/<tool_call>[\s\S]*?<\/tool_call>/gi, '')
  // 孤立开/闭标签残片
  s = s.replace(/<\/?tool_call>/gi, '')
  // 仅含 } 的孤立行（tool_call JSON 尾部残留）：前后均为空行时才删除
  s = s.replace(/\n[ \t]*}[ \t]*(?=\n[ \t]*\n|\n[ \t]*$|$)/g, '')
  return s.trim()
}

function MarkdownRenderer({
  text,
  inOrangeBubble = false,
  streaming = false,
}: {
  text: string
  inOrangeBubble?: boolean
  streaming?: boolean
}): ReactNode {
  const { activeWorkspaceId } = useWorkspaceStore()
  const cleaned = stripToolCallTags(text)
  if (!cleaned) return null

  const lines = cleaned.split('\n')
  const nodes: ReactNode[] = []
  let i = 0
  let keyCounter = 0
  const k = () => `md-${keyCounter++}`

  while (i < lines.length) {
    const line = lines[i]

    // ── 代码块 ──────────────────────────────────────────────────────────────
    if (line.trimStart().startsWith('```')) {
      const lang = line.trim().slice(3).trim()
      const codeLines: string[] = []
      i++
      while (i < lines.length && !lines[i].trimStart().startsWith('```')) {
        codeLines.push(lines[i])
        i++
      }
      i++ // 跳过结束 ```
      nodes.push(
        <div key={k()} className="my-2">
          {lang && (
            <div className={[
              'px-2.5 py-1 rounded-t-lg text-[10px] font-mono font-medium',
              inOrangeBubble ? 'bg-white/20 text-orange-100' : 'bg-zinc-800 text-zinc-400',
            ].join(' ')}>
              {lang}
            </div>
          )}
          <pre className={[
            'px-3 py-2.5 text-[11px] leading-relaxed overflow-x-auto font-mono',
            lang ? 'rounded-b-lg' : 'rounded-lg',
            inOrangeBubble ? 'bg-white/15 text-orange-50' : 'bg-zinc-900 text-zinc-100',
          ].join(' ')}>
            {codeLines.join('\n')}
          </pre>
        </div>
      )
      continue
    }

    // ── 标题 ────────────────────────────────────────────────────────────────
    const h3Match = line.match(/^### (.+)/)
    const h2Match = line.match(/^## (.+)/)
    const h1Match = line.match(/^# (.+)/)
    if (h1Match || h2Match || h3Match) {
      const content = (h1Match?.[1] ?? h2Match?.[1] ?? h3Match?.[1]) ?? ''
      const level = h1Match ? 1 : h2Match ? 2 : 3
      const cls = level === 1
        ? 'text-base font-bold mt-3 mb-1'
        : level === 2
          ? 'text-sm font-semibold mt-2.5 mb-1'
          : 'text-xs font-semibold mt-2 mb-0.5'
      nodes.push(
        <div key={k()} className={[cls, inOrangeBubble ? 'text-white' : 'text-gray-800'].join(' ')}>
          <InlineRenderer text={content} inOrangeBubble={inOrangeBubble} workspaceId={activeWorkspaceId} />
        </div>
      )
      i++
      continue
    }

    // ── 无序列表 ─────────────────────────────────────────────────────────────
    if (/^[\-\*\+] /.test(line)) {
      const listItems: string[] = []
      while (i < lines.length && /^[\-\*\+] /.test(lines[i])) {
        listItems.push(lines[i].replace(/^[\-\*\+] /, ''))
        i++
      }
      nodes.push(
        <ul key={k()} className="my-1 space-y-0.5 pl-3">
          {listItems.map((item, idx) => (
            <li key={idx} className={[
              'flex gap-2 text-sm leading-relaxed',
              inOrangeBubble ? 'text-orange-50' : 'text-gray-700',
            ].join(' ')}>
              <span className={inOrangeBubble ? 'text-orange-200 mt-0.5' : 'text-orange-400 mt-0.5'}>•</span>
              <span><InlineRenderer text={item} inOrangeBubble={inOrangeBubble} workspaceId={activeWorkspaceId} /></span>
            </li>
          ))}
        </ul>
      )
      continue
    }

    // ── 有序列表 ─────────────────────────────────────────────────────────────
    if (/^\d+\. /.test(line)) {
      const listItems: string[] = []
      let num = 1
      while (i < lines.length && /^\d+\. /.test(lines[i])) {
        listItems.push(lines[i].replace(/^\d+\. /, ''))
        i++
        num++
      }
      nodes.push(
        <ol key={k()} className="my-1 space-y-0.5 pl-3">
          {listItems.map((item, idx) => (
            <li key={idx} className={[
              'flex gap-2 text-sm leading-relaxed',
              inOrangeBubble ? 'text-orange-50' : 'text-gray-700',
            ].join(' ')}>
              <span className={[
                'shrink-0 font-mono text-[10px] mt-0.5 w-4 text-right',
                inOrangeBubble ? 'text-orange-200' : 'text-orange-400',
              ].join(' ')}>{idx + 1}.</span>
              <span><InlineRenderer text={item} inOrangeBubble={inOrangeBubble} workspaceId={activeWorkspaceId} /></span>
            </li>
          ))}
        </ol>
      )
      continue
    }

    // ── 分隔线 ──────────────────────────────────────────────────────────────
    if (/^---+$/.test(line.trim())) {
      nodes.push(
        <hr key={k()} className={[
          'my-2 border-0 h-px',
          inOrangeBubble ? 'bg-white/20' : 'bg-gray-100',
        ].join(' ')} />
      )
      i++
      continue
    }

    // ── 空行 ────────────────────────────────────────────────────────────────
    if (line.trim() === '') {
      nodes.push(<div key={k()} className="h-1.5" />)
      i++
      continue
    }

    // ── 普通段落 ─────────────────────────────────────────────────────────────
    nodes.push(
      <p key={k()} className={[
        'text-sm leading-relaxed',
        inOrangeBubble ? 'text-white' : 'text-gray-800',
      ].join(' ')}>
        <InlineRenderer text={line} inOrangeBubble={inOrangeBubble} workspaceId={activeWorkspaceId} />
        {/* 流式光标：只在最后一行末尾显示 */}
        {streaming && i === lines.length - 1 && (
          <span className={[
            'inline-block w-0.5 h-[1em] ml-px animate-pulse align-text-bottom rounded-full',
            inOrangeBubble ? 'bg-orange-100' : 'bg-gray-400',
          ].join(' ')} />
        )}
      </p>
    )
    i++
  }

  return <>{nodes}</>
}

/** 行内元素渲染：粗体、斜体、行内代码、链接、[@文件名] chip */
function InlineRenderer({
  text,
  inOrangeBubble,
  workspaceId,
}: {
  text: string
  inOrangeBubble: boolean
  workspaceId: string | null
}): ReactNode {
  const nodes: ReactNode[] = []
  // 解析顺序：[@文件名] > [附件:] > **粗体** > *斜体* > `代码` > [链接](url)
  const parts = text.split(
    /(\[@[^\]]+\]|\[附件:[^\]]+\]|\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g
  )

  parts.forEach((part, i) => {
    const key = `il-${i}`
    if (!part) return

    if (part.startsWith('[@') && part.endsWith(']')) {
      // 支持两种格式：
      //   [@文件名]          → label=文件名, path=文件名（旧格式兼容）
      //   [@路径/文件名.ext]  → label=文件名, path=路径/文件名.ext（完整路径）
      const inner = part.slice(2, -1)
      const isPath = inner.includes('/')
      const label = isPath ? (inner.split('/').pop() ?? inner) : inner
      const path  = inner
      nodes.push(
        <FileChip key={key} label={label} path={path} size={0}
          workspaceId={workspaceId ?? undefined} inOrangeBubble={inOrangeBubble} />
      )
    } else if (part.startsWith('[附件:') && part.endsWith(']')) {
      const inner = part.slice(4, -1).trim()
      const isPath = inner.includes('/')
      const label = isPath ? (inner.split('/').pop() ?? inner) : inner
      nodes.push(
        <FileChip key={key} label={label} path={inner} size={0}
          workspaceId={workspaceId ?? undefined} inOrangeBubble={inOrangeBubble} />
      )
    } else if (part.startsWith('**') && part.endsWith('**')) {
      nodes.push(
        <strong key={key} className={inOrangeBubble ? 'font-bold text-white' : 'font-semibold text-gray-900'}>
          {part.slice(2, -2)}
        </strong>
      )
    } else if (part.startsWith('*') && part.endsWith('*') && part.length > 2) {
      nodes.push(
        <em key={key} className={inOrangeBubble ? 'italic text-orange-100' : 'italic text-gray-700'}>
          {part.slice(1, -1)}
        </em>
      )
    } else if (part.startsWith('`') && part.endsWith('`')) {
      nodes.push(
        <code key={key} className={[
          'px-1 py-0.5 rounded text-[11px] font-mono',
          inOrangeBubble ? 'bg-white/20 text-orange-50' : 'bg-orange-50 text-orange-600',
        ].join(' ')}>
          {part.slice(1, -1)}
        </code>
      )
    } else {
      const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
      if (linkMatch) {
        nodes.push(
          <a key={key} href={linkMatch[2]} target="_blank" rel="noopener noreferrer"
            className={inOrangeBubble
              ? 'text-orange-100 underline underline-offset-2 hover:text-white transition-colors'
              : 'text-orange-500 underline underline-offset-2 hover:text-orange-700 transition-colors'
            }>
            {linkMatch[1]}
          </a>
        )
      } else {
        nodes.push(<span key={key}>{part}</span>)
      }
    }
  })
  return <>{nodes}</>
}

// ─── 工具结果映射 ─────────────────────────────────────────────────────────────

function buildToolResultMap(
  messages: ChatMessage[],
  assistantIdx: number,
  assistant: ChatAssistantMessage,
): Map<string, ChatToolMessage> {
  const map = new Map<string, ChatToolMessage>()
  if (!assistant.tool_calls?.length) return map
  let j = assistantIdx + 1
  while (j < messages.length && messages[j].role === 'tool') {
    const t = messages[j] as ChatToolMessage
    map.set(t.tool_call_id, t)
    j++
  }
  return map
}

function collectMergedToolIds(messages: ChatMessage[]): Set<string> {
  const consumed = new Set<string>()
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i]
    if (m.role !== 'assistant') continue
    const a = m as ChatAssistantMessage
    if (!a.tool_calls?.length) continue
    let j = i + 1
    while (j < messages.length && messages[j].role === 'tool') {
      consumed.add(messages[j].id)
      j++
    }
  }
  return consumed
}

// ─── 思考过程组件（流式展开）─────────────────────────────────────────────────

const ReasoningBlock = memo(function ReasoningBlock({
  content,
  streaming = false,
}: {
  content: string
  streaming?: boolean
}) {
  const [open, setOpen] = useState(streaming) // 流式时自动展开
  const preRef = useRef<HTMLPreElement>(null)

  // 流式时自动滚到底
  useEffect(() => {
    if (streaming && open && preRef.current) {
      preRef.current.scrollTop = preRef.current.scrollHeight
    }
  }, [content, streaming, open])

  // 流式结束后自动折叠
  useEffect(() => {
    if (!streaming && open) {
      const t = setTimeout(() => setOpen(false), 1200)
      return () => clearTimeout(t)
    }
  }, [streaming, open])

  return (
    <div className="mb-1.5">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 text-[11px] text-gray-400 hover:text-gray-600 transition-colors select-none group"
      >
        <svg
          className={[
            'w-2.5 h-2.5 transition-transform duration-200',
            open ? 'rotate-90' : '',
            streaming ? 'text-orange-400 animate-pulse' : 'text-gray-300',
          ].join(' ')}
          fill="none" stroke="currentColor" viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M9 5l7 7-7 7" />
        </svg>
        <span className={streaming ? 'text-orange-400' : ''}>
          {streaming ? '思考中...' : '思考过程'}
        </span>
        {streaming && (
          <span className="flex gap-0.5">
            {[0, 1, 2].map((n) => (
              <span key={n} className="w-1 h-1 rounded-full bg-orange-400 animate-bounce"
                style={{ animationDelay: `${n * 150}ms` }} />
            ))}
          </span>
        )}
      </button>

      <div className={[
        'overflow-hidden transition-all duration-300',
        open ? 'max-h-48 opacity-100 mt-1.5' : 'max-h-0 opacity-0',
      ].join(' ')}>
        <pre
          ref={preRef}
          className="p-2.5 bg-gradient-to-br from-gray-50 to-gray-100/50 rounded-xl text-[11px] text-gray-500 font-mono whitespace-pre-wrap max-h-44 overflow-y-auto border border-gray-100/80 leading-relaxed"
        >
          {content}
          {streaming && (
            <span className="inline-block w-px h-3 bg-orange-400 ml-0.5 animate-pulse align-text-bottom" />
          )}
        </pre>
      </div>
    </div>
  )
})

// ─── 复制按钮 ────────────────────────────────────────────────────────────────

function CopyButton({ text, inOrangeBubble = false }: { text: string; inOrangeBubble?: boolean }) {
  const [copied, setCopied] = useState(false)

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {
      // 降级：创建临时 textarea
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      document.execCommand('copy')
      document.body.removeChild(ta)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    }
  }, [text])

  return (
    <button
      onClick={handleCopy}
      title={copied ? '已复制' : '复制消息'}
      className={[
        'shrink-0 w-6 h-6 flex items-center justify-center rounded-lg transition-all duration-150',
        inOrangeBubble
          ? 'text-orange-200 hover:text-white hover:bg-white/20'
          : 'text-gray-300 hover:text-gray-500 hover:bg-gray-100',
        copied ? (inOrangeBubble ? 'text-white bg-white/20' : 'text-green-500 bg-green-50') : '',
      ].join(' ')}
    >
      {copied ? (
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 13l4 4L19 7" />
        </svg>
      ) : (
        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.8}
            d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z" />
        </svg>
      )}
    </button>
  )
}

// ─── 用户气泡 ─────────────────────────────────────────────────────────────────

const UserBubble = memo(function UserBubble({ content }: { content: string }) {
  const hasRef = content.includes('[@') || content.includes('[附件:')
  const [hovered, setHovered] = useState(false)
  return (
    <div
      className="flex justify-end items-end gap-1.5 group"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* 复制按钮（hover 时显示，在气泡左侧） */}
      <div className={['transition-opacity duration-150', hovered ? 'opacity-100' : 'opacity-0'].join(' ')}>
        <CopyButton text={content} inOrangeBubble={false} />
      </div>
      <div className={[
        'max-w-[85%] rounded-2xl rounded-tr-sm px-3.5 py-2.5 text-sm leading-relaxed',
        'shadow-sm bg-gradient-to-br from-orange-500 to-orange-600 text-white shadow-orange-200/60',
      ].join(' ')}>
        {hasRef
          ? <MarkdownRenderer text={content} inOrangeBubble streaming={false} />
          : content
        }
      </div>
    </div>
  )
})

// ─── 助手气泡 ─────────────────────────────────────────────────────────────────

interface AssistantBubbleProps {
  m: ChatAssistantMessage
  streaming?: boolean
  toolResults?: Map<string, ChatToolMessage>
}

const AssistantBubble = memo(function AssistantBubble({
  m,
  streaming = false,
  toolResults,
}: AssistantBubbleProps) {
  const hasContent   = !!m.content?.trim()
  const hasTools     = !!m.tool_calls?.length
  const hasReasoning = !!m.reasoning_content?.trim()
  const [hovered, setHovered] = useState(false)

  // 构建可复制的纯文本：正文 + 工具调用摘要
  const copyText = [
    m.content?.trim() ?? '',
    ...(m.tool_calls?.map((tc) => {
      const result = toolResults?.get(tc.id)
      const preview = result?.content ? ` → ${result.content.slice(0, 200)}` : ''
      return `[工具: ${tc.function.name}${preview}]`
    }) ?? []),
  ].filter(Boolean).join('\n')

  return (
    <div
      className="flex justify-start gap-2 group"
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {/* 头像 */}
      <div className="shrink-0 w-6 h-6 rounded-full bg-gradient-to-br from-orange-400 to-amber-500 flex items-center justify-center text-white text-xs mt-0.5 shadow-sm shadow-orange-200">
        ✦
      </div>

      <div className="max-w-[88%] space-y-1.5 min-w-0">
        {/* 思考过程 */}
        {hasReasoning && (
          <ReasoningBlock content={m.reasoning_content ?? ''} streaming={streaming && !hasContent} />
        )}

        {/* 正文 */}
        {hasContent && (
          <div className={[
            'rounded-2xl rounded-tl-sm border bg-white px-3.5 py-2.5 shadow-sm',
            streaming ? 'border-orange-100 shadow-orange-50' : 'border-gray-100',
          ].join(' ')}>
            <MarkdownRenderer text={m.content ?? ''} streaming={streaming} />
          </div>
        )}

        {/* 工具调用卡片 */}
        {hasTools && m.tool_calls?.map((tc) => {
          const result = toolResults?.get(tc.id)
          return (
            <ToolCard
              key={tc.id}
              toolName={tc.function.name}
              argumentsJson={tc.function.arguments}
              resultText={result?.content}
              status={
                streaming && !result ? 'running'
                  : result?.content?.startsWith('失败:') ? 'failed'
                    : 'succeeded'
              }
              error={result?.content?.startsWith('失败:') ? result.content.slice(3) : undefined}
            />
          )
        })}

        {/* 复制按钮（hover 时显示，在内容下方） */}
        {!streaming && copyText && (
          <div className={['flex transition-opacity duration-150', hovered ? 'opacity-100' : 'opacity-0'].join(' ')}>
            <CopyButton text={copyText} />
          </div>
        )}
      </div>
    </div>
  )
})

// ─── 流式助手卡（含多轮进度感知）────────────────────────────────────────────

export const StreamingAssistantCard = memo(function StreamingAssistantCard({
  content,
  reasoningContent,
  toolCalls,
  roundIdx,
}: {
  content: string
  reasoningContent?: string
  toolCalls: { id: string; type: 'function'; function: { name: string; arguments: string } }[]
  roundIdx?: number
}) {
  const m: ChatAssistantMessage = {
    id: '__streaming__',
    role: 'assistant',
    content,
    reasoning_content: reasoningContent,
    tool_calls: toolCalls.length ? toolCalls : undefined,
    createdAt: new Date().toISOString(),
    kind: 'turn',
  }

  return (
    <div className="space-y-1.5">
      {/* 多轮进度标签（第 N 轮 ReAct）*/}
      {typeof roundIdx === 'number' && roundIdx > 0 && (
        <div className="flex items-center gap-1.5 pl-8">
          <span className="text-[10px] text-orange-400 font-mono tabular-nums">
            第 {roundIdx + 1} 轮
          </span>
          <span className="flex gap-0.5">
            {[0, 1, 2].map((n) => (
              <span key={n} className="w-1 h-1 rounded-full bg-orange-300 animate-bounce"
                style={{ animationDelay: `${n * 120}ms` }} />
            ))}
          </span>
        </div>
      )}
      <AssistantBubble m={m} streaming />
    </div>
  )
})

// ─── ChatMessageList ──────────────────────────────────────────────────────────

interface Props {
  messages: ChatMessage[]
}

export const ChatMessageList = memo(function ChatMessageList({ messages }: Props) {
  const mergedToolIds = useMemo(() => collectMergedToolIds(messages), [messages])

  return (
    <div className="space-y-3">
      {messages.map((m, index) => {
        // tool message：若已被 assistant 合并展示则隐藏，否则独立显示
        if (m.role === 'tool') {
          if (mergedToolIds.has(m.id)) return <Fragment key={m.id} />
          return (
            <div key={m.id} className="flex justify-start pl-8">
              <ToolCard
                toolName={(m as ChatToolMessage).name ?? m.id}
                argumentsJson="{}"
                resultText={(m as ChatToolMessage).content}
                status={(m as ChatToolMessage).content?.startsWith('失败:') ? 'failed' : 'succeeded'}
              />
            </div>
          )
        }
        if (m.role === 'user') {
          return <UserBubble key={m.id} content={m.content} />
        }
        if (m.role === 'assistant') {
          const toolResults = buildToolResultMap(messages, index, m as ChatAssistantMessage)
          return (
            <AssistantBubble
              key={m.id}
              m={m as ChatAssistantMessage}
              toolResults={toolResults}
            />
          )
        }
        return null
      })}
    </div>
  )
})
