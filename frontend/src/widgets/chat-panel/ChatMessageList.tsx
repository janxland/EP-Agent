'use client'

import React, { Fragment, memo, useMemo } from 'react'

// ─── 轻量 Markdown 渲染（无外部依赖）────────────────────────────────────────
// 支持：**粗体**、`行内代码`、[链接](url)、换行
function renderMarkdown(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = []
  // 按换行先分行
  const lines = text.split('\n')
  lines.forEach((line, lineIdx) => {
    if (lineIdx > 0) nodes.push(<br key={`br-${lineIdx}`} />)
    // 逐段解析行内语法
    const parts = line.split(/(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\([^)]+\))/g)
    parts.forEach((part, i) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        nodes.push(<strong key={`${lineIdx}-b-${i}`}>{part.slice(2, -2)}</strong>)
      } else if (part.startsWith('`') && part.endsWith('`')) {
        nodes.push(
          <code key={`${lineIdx}-c-${i}`}
            className="px-1 py-0.5 bg-gray-100 rounded text-[11px] font-mono text-orange-600">
            {part.slice(1, -1)}
          </code>
        )
      } else {
        const linkMatch = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/)
        if (linkMatch) {
          nodes.push(
            <a key={`${lineIdx}-l-${i}`} href={linkMatch[2]} target="_blank" rel="noopener noreferrer"
              className="text-orange-500 underline hover:text-orange-700 transition-colors">
              {linkMatch[1]}
            </a>
          )
        } else if (part) {
          nodes.push(<span key={`${lineIdx}-t-${i}`}>{part}</span>)
        }
      }
    })
  })
  return nodes
}
import type {
  ChatMessage,
  ChatAssistantMessage,
  ChatToolMessage,
} from '@/features/chat/types/chat.types'
import { ToolCard } from './tool-call/ToolCard'

// ─── 工具结果映射（O(1) 精确匹配）────────────────────────────────────────────
//
// assistant 消息的 tool_calls 数组可能有多个并发调用；
// 紧跟其后的若干 tool messages 通过 tool_call_id 一一对应。

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

/** 收集已被 assistant 合并展示的 tool message ID，避免重复渲染 */
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

// ─── 用户气泡 ─────────────────────────────────────────────────────────────────

const UserBubble = memo(function UserBubble({ content }: { content: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-tr-sm bg-orange-500 text-white px-3.5 py-2.5 text-sm leading-relaxed shadow-sm shadow-orange-100">
        {content}
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

  return (
    <div className="flex justify-start">
      {/* 头像 */}
      <div className="shrink-0 w-6 h-6 rounded-full bg-gradient-to-br from-orange-400 to-amber-400 flex items-center justify-center text-white text-xs mr-2 mt-0.5 shadow-sm">
        ✦
      </div>

      <div className="max-w-[88%] space-y-2 min-w-0">

        {/* 思考过程（可折叠） */}
        {hasReasoning && (
          <details className="group">
            <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none flex items-center gap-1 list-none">
              <svg
                className="w-2.5 h-2.5 transition-transform group-open:rotate-90 text-gray-300"
                fill="none" stroke="currentColor" viewBox="0 0 24 24"
              >
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
              </svg>
              <span>思考过程</span>
            </summary>
            <pre className="mt-1.5 p-2.5 bg-gray-50 rounded-xl text-[11px] text-gray-500 font-mono whitespace-pre-wrap max-h-36 overflow-y-auto border border-gray-100 leading-relaxed">
              {m.reasoning_content}
            </pre>
          </details>
        )}

        {/* 文本气泡（支持 Markdown 渲染：**粗体**、`代码`、[链接](url)）*/}
        {hasContent && (
          <div className="rounded-2xl rounded-tl-sm border border-gray-100 bg-white px-3.5 py-2.5 text-sm leading-relaxed text-gray-800 shadow-sm">
            {renderMarkdown(m.content ?? '')}
            {streaming && (
              <span className="inline-block w-0.5 h-[1em] bg-gray-400 ml-0.5 animate-pulse align-text-bottom" />
            )}
          </div>
        )}

        {/* 工具调用卡片（并发工具调用依次渲染） */}
        {hasTools && m.tool_calls?.map((tc) => {
          const result = toolResults?.get(tc.id)
          return (
            <ToolCard
              key={tc.id}
              toolName={tc.function.name}
              argumentsJson={tc.function.arguments}
              resultText={result?.content}
              status={
                streaming && !result
                  ? 'running'
                  : result?.content?.startsWith('失败:')
                    ? 'failed'
                    : 'succeeded'
              }
              error={result?.content?.startsWith('失败:') ? result.content.slice(3) : undefined}
            />
          )
        })}
      </div>
    </div>
  )
})

// ─── 流式助手卡（实时渲染，未 commit）────────────────────────────────────────

export const StreamingAssistantCard = memo(function StreamingAssistantCard({
  content,
  reasoningContent,
  toolCalls,
}: {
  content: string
  reasoningContent?: string
  toolCalls: { id: string; type: 'function'; function: { name: string; arguments: string } }[]
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
  return <AssistantBubble m={m} streaming />
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

        // tool message 已被对应 assistant 合并展示，跳过独立渲染
        if (m.role === 'tool') {
          if (mergedToolIds.has(m.id)) return <Fragment key={m.id} />
          // 未合并的孤立 tool message（兜底展示）
          return (
            <div key={m.id} className="flex justify-start pl-8">
              <ToolCard
                toolName={(m as ChatToolMessage).name ?? m.id}
                argumentsJson="{}"
                resultText={(m as ChatToolMessage).content}
                status={
                  (m as ChatToolMessage).content?.startsWith('失败:')
                    ? 'failed'
                    : 'succeeded'
                }
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
