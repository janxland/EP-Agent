/**
 * useBackendHealth — 后端健康检查 Hook
 *
 * 职责：
 *   - 定期 ping /api/health，感知后端服务状态
 *   - 返回 status: 'ok' | 'degraded' | 'down' | 'unknown'
 *   - 首次挂载立即检查，之后按 intervalMs 轮询
 *
 * 使用方式（ChatPanel 顶栏）：
 *   const health = useBackendHealth()
 *   // health.status / health.toolCount / health.domainCount
 *
 * 状态说明：
 *   ok       — 服务正常，所有工具可用
 *   degraded — 服务响应但部分工具未注册（tools_ok=false）
 *   down     — 服务无响应（网络错误/超时）
 *   unknown  — 初始状态，首次检查尚未完成
 */

import { useCallback, useEffect, useRef, useState } from 'react'

export type BackendHealthStatus = 'ok' | 'degraded' | 'down' | 'unknown'

export interface BackendHealth {
  status:      BackendHealthStatus
  toolCount:   number
  domainCount: number
  lastChecked: Date | null
}

const INITIAL_HEALTH: BackendHealth = {
  status:      'unknown',
  toolCount:   0,
  domainCount: 0,
  lastChecked: null,
}

export function useBackendHealth(intervalMs = 60_000): BackendHealth {
  const [health, setHealth] = useState<BackendHealth>(INITIAL_HEALTH)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const check = useCallback(async () => {
    try {
      const res = await fetch('/api/health', {
        signal: AbortSignal.timeout(3000),
        cache:  'no-store',
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setHealth({
        status:      data.tools_ok ? 'ok' : 'degraded',
        toolCount:   data.tool_count   ?? 0,
        domainCount: data.domain_count ?? 0,
        lastChecked: new Date(),
      })
    } catch {
      setHealth((prev) => ({
        ...prev,
        status:      'down',
        lastChecked: new Date(),
      }))
    }
  }, [])

  useEffect(() => {
    // 立即检查一次
    check()
    // 低频轮询（仅用于感知后端恢复，正常情况不需要频繁检查）
    timerRef.current = setInterval(check, intervalMs)
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [check, intervalMs])

  return health
}

// ── 健康状态视觉配置（供 ChatPanel 顶栏使用）──────────────────────────────────

export const HEALTH_VISUAL: Record<BackendHealthStatus, {
  dot:   string   // Tailwind class
  tip:   string   // tooltip 文字
  show:  boolean  // 是否在顶栏显示（ok 时隐藏，异常时才显示）
}> = {
  ok:      { dot: 'bg-green-400',              tip: '服务正常',       show: false },
  degraded:{ dot: 'bg-yellow-400',             tip: '部分工具不可用', show: true  },
  down:    { dot: 'bg-red-400 animate-pulse',  tip: '服务不可用',     show: true  },
  unknown: { dot: 'bg-gray-300',               tip: '检查中…',        show: false },
}
