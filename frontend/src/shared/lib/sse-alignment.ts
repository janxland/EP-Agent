/**
 * SSE 对齐检查器 + 序号守卫
 *
 * 职责：
 *   1. checkSSEAlignment — 检测前端未处理的 SSE 事件类型（开发模式警告）
 *   2. SequenceGuard     — 检测 SSE 事件乱序/重复（健壮性）
 *
 * 使用方式：
 *   // chat.store.ts handleSSEEvent default 分支
 *   default:
 *     checkSSEAlignment(event.type)
 *     break
 *
 *   // api.ts SSE 接收入口
 *   const guard = new SequenceGuard()
 *   guard.reset()  // 每次 startRun 时重置
 *   const result = guard.check(event)
 *   if (result !== 'ok') console.warn('[SSE]', result, event)
 */

import type { SSEEventType } from '@/shared/types'

// ── 前端已处理的 SSE 事件类型集合（与 chat.store.ts handleSSEEvent 保持同步）──

export const SSE_HANDLED_EVENTS = new Set<string>([
  'connected',
  'pipeline.step',
  'pipeline.state',           // 后端 session 运行状态同步（replay 时解除 loading）
  'abc.updated',
  'activity.update',
  'message.delta',
  'message.completed',
  'message.history',
  'tool.call',
  'todo.list',
  'todo.update',
  'todo.append',
  'role.active',              // 角色激活（切换角色/刷新恢复）
  'h5.ready',                 // H5 海报生成完成（含 url_path/file_path/size_kb）
  'connection.reconnecting',  // SSE 断线重连通知（api.ts 发出，chat.store 清空历史）
  'error',
])

/**
 * 检查 SSE 事件类型是否已在前端注册处理分支。
 * 开发模式下打印警告，生产模式静默（不影响用户体验）。
 */
export function checkSSEAlignment(eventType: string): void {
  if (SSE_HANDLED_EVENTS.has(eventType)) return
  if (process.env.NODE_ENV === 'development') {
    console.warn(
      `[SSE对齐] 未处理的事件类型: "${eventType}"`,
      '\n→ 请在 chat.store.ts 的 handleSSEEvent switch 中补充处理分支',
      '\n→ 并将 "${eventType}" 添加到 SSE_HANDLED_EVENTS 集合',
    )
  }
}

/**
 * 注册新的 SSE 事件类型（后端新增事件时调用）。
 * 调用后 checkSSEAlignment 不再对此类型发出警告。
 */
export function registerSSEEvent(eventType: SSEEventType | string): void {
  SSE_HANDLED_EVENTS.add(eventType)
}

// ── 序号守卫 ─────────────────────────────────────────────────────────────────

export type SequenceCheckResult = 'ok' | 'duplicate' | 'out-of-order'

/**
 * SSE 序号守卫。
 * 检测乱序（sequence 回退）和重复（同一 id 出现两次）。
 * 每次 startRun 时调用 reset() 重置状态。
 */
export class SequenceGuard {
  private lastSeq = -1
  private seenIds = new Set<string>()

  check(event: { id?: string; sequence?: number; payload?: { _replay?: boolean } }): SequenceCheckResult {
    // 去重：同一 id 不处理两次
    if (event.id && this.seenIds.has(event.id)) return 'duplicate'
    if (event.id) this.seenIds.add(event.id)

    // FE-5: replay 事件携带 _replay=true，序列号从历史基准继续递增，
    // 但重连时前端 lastSeq 已重置为 -1，直接接受即可，无需校验乱序。
    if (event.payload?._replay) return 'ok'

    // 序号检查（sequence 字段存在且 > 0 时才检查）
    if (event.sequence !== undefined && event.sequence > 0) {
      if (event.sequence <= this.lastSeq) return 'out-of-order'
      this.lastSeq = event.sequence
    }
    return 'ok'
  }

  reset(): void {
    this.lastSeq = -1
    this.seenIds.clear()
  }
}

// 全局单例（供 chatUniversal 的 SSE 接收循环使用）
export const globalSequenceGuard = new SequenceGuard()
