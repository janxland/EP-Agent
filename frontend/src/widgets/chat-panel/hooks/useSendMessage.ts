'use client'

/**
 * useSendMessage — 消息发送/中断逻辑 hook
 *
 * 职责：
 *   - 防重复发送锁（useRef，不触发 re-render）
 *   - 超时兜底（REQUEST_TIMEOUT_MS）
 *   - 中断对话（abortRun）
 *   - 附件组装 → chatUniversal 调用
 *
 * 与 ChatPanel 解耦：ChatPanel 只需调用 handleSend / handleAbort。
 */

import { useRef, useEffect, useCallback } from 'react'
import { useChatStore } from '@/features/chat/store/chat.store'
import { chatUniversal } from '@/shared/lib/api'
import type { Attachment } from './useAttachment'

// POST 请求本身的超时（后端接受请求前）：30s 足够
// 注意：后端返回 202 Accepted 后立即清除此计时器，任务执行时长由 SSE 事件驱动，
// 不再用固定超时兜底（避免长任务被误判为超时）
const REQUEST_TIMEOUT_MS = 30_000

interface UseSendMessageOptions {
  sessionId: string | null
  resolvedWorkspaceId: string
  resolvedProjectId: string
}

export function useSendMessage({
  sessionId,
  resolvedWorkspaceId,
  resolvedProjectId,
}: UseSendMessageOptions) {
  const {
    status,
    addOptimisticUserMessage,
    startRun,
    failRun,
    abortRun,
  } = useChatStore()

  // ── 防重复发送锁（同步，彻底杜绝并发重复请求）────────────────────────────
  const inflightRef = useRef(false)
  const timeoutRef  = useRef<ReturnType<typeof setTimeout> | null>(null)

  // status 从 running → 其他 时，自动释放锁并清理超时
  useEffect(() => {
    if (status !== 'running') {
      inflightRef.current = false
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
    }
  }, [status])

  // status ref — 避免 handleSend 闭包依赖 status 导致函数频繁重建
  const statusRef = useRef(status)
  statusRef.current = status

  const handleSend = useCallback(async (
    text: string,
    attachment: Attachment | null,
  ) => {
    if (!text.trim() || !sessionId) return
    if (inflightRef.current || statusRef.current === 'running') return
    inflightRef.current = true

    // 胶囊引用：始终使用 workspace_path
    const attRef = attachment
      ? `[@${attachment.workspace_path || attachment.name}]`
      : ''
    const displayText = (attachment && attRef && !text.includes(attRef))
      ? `${text} ${attRef}`
      : text

    addOptimisticUserMessage(displayText)
    startRun()

    timeoutRef.current = setTimeout(() => {
      inflightRef.current = false
      failRun('请求超时，请检查后端连接')
    }, REQUEST_TIMEOUT_MS)

    const isBinary = attachment?.kind === 'midi' || attachment?.kind === 'audio' || attachment?.kind === 'image'

    chatUniversal(sessionId, {
      message: displayText,
      workspace_id: resolvedWorkspaceId,
      project_id:   resolvedProjectId,
      attachment_name:           attachment?.name           ?? '',
      attachment_workspace_path: attachment?.workspace_path ?? '',
      attachment_content:        (!isBinary && attachment?.content) ? attachment.content : '',
      attachment_b64:            '',
    }).then(() => {
      // POST 成功（202 Accepted）：后端已接受任务，清除超时计时器。
      // 任务执行时长不可预测（LLM 多轮调用可能数分钟），
      // 结束信号由 SSE message.completed / error / graph.error 驱动，
      // 不能再用固定超时兜底，否则长任务会被误判为"请求超时"。
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
        timeoutRef.current = null
      }
    }).catch((e: unknown) => {
      // POST 本身失败（网络错误、后端 5xx 等）：立即报错
      const msg = e instanceof Error ? e.message : '请求失败'
      inflightRef.current = false
      failRun(msg)
    })
  // status 通过 ref 读取，不再列入依赖——避免每次 status 变化都重建函数引用，减少 InputBox re-render
  }, [sessionId, resolvedWorkspaceId, resolvedProjectId,
      addOptimisticUserMessage, startRun, failRun])

  const handleAbort = useCallback(() => {
    if (!sessionId) return
    void abortRun(sessionId)
  }, [sessionId, abortRun])

  return { handleSend, handleAbort, isRunning: status === 'running' }
}
