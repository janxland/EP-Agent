/**
 * Timeline Store — 审计链路状态管理 v2.1
 *
 * 新增：
 *   - filterDomain / filterKeyword：前端过滤（无需重新请求）
 *   - loadMore：分页加载更多 trace
 *   - autoRefresh：对话完成后自动刷新（监听 window 事件）
 *   - filteredTraces：派生过滤结果（computed-like selector）
 *   - stats: TraceStats 统计摘要
 *   - startReplay 完成后派发 ep:replay-done 事件，ReplayPanel 自动刷新历史
 *   - replayResult 使用完整 ReplayResponse 类型（含 fixture_hit_rate / token_diff 等）
 */
import { create } from 'zustand'
import { listSessionTraces, getTraceDetail, replayTrace, replayTraceStream, getSessionTraceStats } from '@/shared/lib/api'
import type { TraceDto, SpanDto } from '@/shared/types/trace.types'
import type { ReplayResponse, ReplayStepEvent } from '@/shared/lib/api'

// 模块级 AbortController 引用（不放入 store state，避免触发不必要的 re-render）
let _replayAC: AbortController | null = null

// 每页加载条数
const PAGE_SIZE = 20

/** 审计统计摘要（来自 /traces/stats API） */
export interface TraceStats {
  total: number
  succeeded: number
  failed: number
  running: number
  total_input_tokens: number
  total_output_tokens: number
  avg_duration_ms: number
}

interface TimelineState {
  // ── 数据 ──────────────────────────────────────────────────────
  traces: TraceDto[]
  selectedTraceId: string | null
  spans: SpanDto[]
  /** 当前已加载条数（用于分页） */
  loadedCount: number
  /** 是否还有更多可加载 */
  hasMore: boolean
  // ── 统计摘要 ──────────────────────────────────────────────────
  stats: TraceStats | null
  // ── 过滤 ──────────────────────────────────────────────────────
  filterDomain: string          // '' = 全部
  filterKeyword: string         // 搜索关键词（匹配 user_message）
  // ── 重播状态 ──────────────────────────────────────────────────
  replayingTraceId: string | null
  replayResult: ReplayResponse | null
  /** 流式重放：实时步骤进度列表 */
  replaySteps: ReplayStepEvent[]
  /** 流式重放：当前正在执行的步骤描述 */
  replayCurrentStep: string | null
  // ── UI 状态 ───────────────────────────────────────────────────
  isOpen: boolean
  loading: boolean
  loadingMore: boolean
  error: string | null
  // ── 面板宽度 ──────────────────────────────────────────────────
  panelWidth: number            // px，默认 680，可拖拽调整

  // ── Actions ───────────────────────────────────────────────────
  openPanel: () => void
  closePanel: () => void
  togglePanel: () => void
  loadTraces: (sessionId: string, merge?: boolean) => Promise<void>
  loadMore: (sessionId: string) => Promise<void>
  loadStats: (sessionId: string) => Promise<void>
  selectTrace: (traceId: string) => Promise<void>
  clearTrace: () => void
  startReplay: (traceId: string, mode?: 'fixture' | 'live') => void
  clearReplay: () => void
  abortReplay: () => void
  setFilterDomain: (domain: string) => void
  setFilterKeyword: (kw: string) => void
  setPanelWidth: (w: number) => void
  reset: () => void

  // ── 派生：过滤后的 trace 列表 ────────────────────────────────
  filteredTraces: () => TraceDto[]
}

export const useTimelineStore = create<TimelineState>((set, get) => ({
  traces: [],
  selectedTraceId: null,
  spans: [],
  loadedCount: 0,
  hasMore: false,
  stats: null,
  filterDomain: '',
  filterKeyword: '',
  replayingTraceId: null,
  replayResult: null,
  replaySteps: [],
  replayCurrentStep: null,
  isOpen: false,
  loading: false,
  loadingMore: false,
  error: null,
  panelWidth: 680,

  openPanel: () => set({ isOpen: true }),
  closePanel: () => set({ isOpen: false }),
  togglePanel: () => set((s) => ({ isOpen: !s.isOpen })),

  setPanelWidth: (w) => set({ panelWidth: Math.max(480, Math.min(1200, w)) }),

  setFilterDomain: (domain) => set({ filterDomain: domain }),
  setFilterKeyword: (kw) => set({ filterKeyword: kw }),

  /** 派生：前端过滤（domain + keyword），避免重复网络请求 */
  filteredTraces: () => {
    const { traces, filterDomain, filterKeyword } = get()
    let result = traces
    if (filterDomain) {
      result = result.filter((t) => t.domain === filterDomain)
    }
    if (filterKeyword.trim()) {
      const kw = filterKeyword.trim().toLowerCase()
      result = result.filter(
        (t) => t.user_message?.toLowerCase().includes(kw)
          || t.domain?.toLowerCase().includes(kw)
          || t.role_id?.toLowerCase().includes(kw)
      )
    }
    return result
  },

  loadTraces: async (sessionId: string, merge = false) => {
    set({ loading: true, error: null })
    try {
      const res = await listSessionTraces(sessionId, PAGE_SIZE)
      const incoming = res.traces ?? []
      set((s) => {
        // merge=true（对话完成后自动刷新）：增量合并，已有 trace 保留，新增的追加到头部
        // merge=false（首次打开/手动刷新）：全量替换
        if (merge && s.traces.length > 0) {
          const existingIds = new Set(s.traces.map(t => t.trace_id))
          const newOnes = incoming.filter(t => !existingIds.has(t.trace_id))
          const merged = [...newOnes, ...s.traces]
          return {
            traces: merged,
            loading: false,
            loadedCount: merged.length,
            hasMore: incoming.length >= PAGE_SIZE,
          }
        }
        return {
          traces: incoming,
          loading: false,
          loadedCount: incoming.length,
          hasMore: incoming.length >= PAGE_SIZE,
        }
      })
    } catch (e: unknown) {
      set({ error: String(e), loading: false })
    }
  },

  loadStats: async (sessionId: string) => {
    try {
      const res = await getSessionTraceStats(sessionId)
      if (res.ok) set({ stats: res.stats ?? null })
    } catch {
      // 统计失败不影响主流程，静默忽略
    }
  },

  loadMore: async (sessionId: string) => {
    const { loadedCount, loadingMore } = get()
    if (loadingMore) return
    set({ loadingMore: true })
    try {
      // 利用 offset 加载下一页
      const res = await listSessionTraces(sessionId, PAGE_SIZE, loadedCount)
      const newTraces = res.traces ?? []
      set((s) => ({
        traces: [...s.traces, ...newTraces],
        loadedCount: s.loadedCount + newTraces.length,
        hasMore: newTraces.length >= PAGE_SIZE,
        loadingMore: false,
      }))
    } catch (e: unknown) {
      set({ error: String(e), loadingMore: false })
    }
  },

  selectTrace: async (traceId: string) => {
    if (get().selectedTraceId === traceId) return
    set({ loading: true, error: null, selectedTraceId: traceId, spans: [] })
    try {
      const res = await getTraceDetail(traceId)
      if (get().selectedTraceId !== traceId) return
      set({ spans: res.spans, loading: false })
    } catch (e: unknown) {
      if (get().selectedTraceId !== traceId) return
      set({ error: String(e), loading: false })
    }
  },

  clearTrace: () => set({ selectedTraceId: null, spans: [] }),

  startReplay: (traceId: string, mode: 'fixture' | 'live' = 'fixture') => {
    // 先中止上一次未完成的重放（如有）
    if (_replayAC) {
      _replayAC.abort()
      _replayAC = null
    }
    // 清理上次结果，初始化进度
    set({ replayingTraceId: traceId, replayResult: null, replaySteps: [], replayCurrentStep: '初始化重放…', error: null })

    // 保存 AbortController 引用，供 abortReplay() 真正中止网络请求
    _replayAC = replayTraceStream(traceId, mode, {
      onStep: (evt) => {
        set((s) => ({
          replaySteps: [...s.replaySteps, evt],
          replayCurrentStep: evt.text ?? evt.tool ?? String(evt.step),
        }))
      },
      onDone: (result) => {
        _replayAC = null
        set({ replayResult: result, replayingTraceId: null, replayCurrentStep: null })
        window.dispatchEvent(new CustomEvent('ep:replay-done', { detail: { traceId, replayId: result.replay_id } }))
      },
      onError: (error) => {
        _replayAC = null
        set({ error, replayingTraceId: null, replayCurrentStep: null })
      },
    })
  },

  clearReplay: () => set({ replayResult: null, replayingTraceId: null, replaySteps: [], replayCurrentStep: null }),

  abortReplay: () => {
    // 真正中止 SSE 网络请求
    if (_replayAC) {
      _replayAC.abort()
      _replayAC = null
    }
    set({ replayingTraceId: null, replaySteps: [], replayCurrentStep: null })
  },

  reset: () => set({
    traces: [], selectedTraceId: null, spans: [],
    loadedCount: 0, hasMore: false,
    stats: null,
    filterDomain: '', filterKeyword: '',
    replayingTraceId: null, replayResult: null,
    replaySteps: [], replayCurrentStep: null,
    isOpen: false, loading: false, loadingMore: false, error: null,
    panelWidth: 680,
  }),
}))
