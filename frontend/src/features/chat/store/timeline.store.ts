/**
 * Timeline Store — 审计链路状态管理 v3.0
 *
 * v3.0 新增：
 *   - 三层层级筛选：filterWorkspaceId / filterProjectId / filterSessionId
 *   - 全局查询模式：loadTraces 改为调用 searchTracesGlobal，支持跨对话查看历史审计
 *   - loadStats 改为调用 getGlobalTraceStats，统计摘要随层级筛选变化
 *   - 切换对话时不再自动清空 traces，保持跨 session 可见
 */
import { create } from 'zustand'
import {
  listSessionTraces,
  searchTracesGlobal,
  getTraceDetail,
  replayTrace,
  replayTraceStream,
  getSessionTraceStats,
  getGlobalTraceStats,
} from '@/shared/lib/api'
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
  // ── 三层层级筛选 ──────────────────────────────────────────────
  /** 工作区筛选（'' = 全部） */
  filterWorkspaceId: string
  /** 项目筛选（'' = 全部） */
  filterProjectId: string
  /** 话题/Session 筛选（'' = 全部） */
  filterSessionId: string
  // ── 内容过滤 ──────────────────────────────────────────────────
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
  /**
   * 加载 trace 列表。
   * - 若设置了层级筛选（workspace/project/session），调用全局搜索接口
   * - merge=true：增量追加（对话完成后自动刷新用）
   * - 无任何筛选时也支持全量加载（管理员视角）
   */
  loadTraces: (sessionId?: string, merge?: boolean) => Promise<void>
  loadMore: (sessionId?: string) => Promise<void>
  loadStats: (sessionId?: string) => Promise<void>
  selectTrace: (traceId: string) => Promise<void>
  clearTrace: () => void
  startReplay: (traceId: string, mode?: 'fixture' | 'live') => void
  clearReplay: () => void
  abortReplay: () => void
  /** 设置三层层级筛选，自动触发重新加载 */
  setHierarchyFilter: (wsId: string, projId: string, sessId: string) => void
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
  filterWorkspaceId: '',
  filterProjectId: '',
  filterSessionId: '',
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

  /** 设置三层层级筛选，并立即重新加载 */
  setHierarchyFilter: (wsId, projId, sessId) => {
    set({ filterWorkspaceId: wsId, filterProjectId: projId, filterSessionId: sessId })
    void get().loadTraces(sessId || undefined)
    void get().loadStats(sessId || undefined)
  },

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

  loadTraces: async (sessionId?: string, merge = false) => {
    const { filterWorkspaceId, filterProjectId, filterSessionId } = get()
    // 优先使用 store 中的层级筛选，sessionId 参数作为兜底
    const effectiveSessionId = filterSessionId || sessionId || ''
    const effectiveWorkspaceId = filterWorkspaceId
    const effectiveProjectId = filterProjectId

    set({ loading: true, error: null })
    try {
      let incoming: TraceDto[]

      if (effectiveWorkspaceId || effectiveProjectId || effectiveSessionId) {
        // 有层级筛选：调用全局搜索接口
        const res = await searchTracesGlobal({
          workspace_id: effectiveWorkspaceId || undefined,
          project_id:   effectiveProjectId   || undefined,
          session_id:   effectiveSessionId   || undefined,
          limit: PAGE_SIZE,
          offset: 0,
        })
        incoming = res.traces ?? []
      } else {
        // 无筛选：加载最新 PAGE_SIZE 条（全局视图）
        const res = await searchTracesGlobal({ limit: PAGE_SIZE, offset: 0 })
        incoming = res.traces ?? []
      }

      set((s) => {
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

  loadStats: async (sessionId?: string) => {
    const { filterWorkspaceId, filterProjectId, filterSessionId } = get()
    const effectiveSessionId = filterSessionId || sessionId || ''
    try {
      const res = await getGlobalTraceStats({
        workspace_id: filterWorkspaceId || undefined,
        project_id:   filterProjectId   || undefined,
        session_id:   effectiveSessionId || undefined,
      })
      if (res.ok) set({ stats: res.stats ?? null })
    } catch {
      // 统计失败不影响主流程，静默忽略
    }
  },

  loadMore: async (sessionId?: string) => {
    const { loadedCount, loadingMore, filterWorkspaceId, filterProjectId, filterSessionId } = get()
    if (loadingMore) return
    set({ loadingMore: true })
    const effectiveSessionId = filterSessionId || sessionId || ''
    try {
      const res = await searchTracesGlobal({
        workspace_id: filterWorkspaceId || undefined,
        project_id:   filterProjectId   || undefined,
        session_id:   effectiveSessionId || undefined,
        limit: PAGE_SIZE,
        offset: loadedCount,
      })
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
    if (_replayAC) {
      _replayAC.abort()
      _replayAC = null
    }
    set({ replayingTraceId: traceId, replayResult: null, replaySteps: [], replayCurrentStep: '初始化重放…', error: null })

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
        // BUG-FIX: 后端 replay.done 帧无 replay_id 字段，用 session_id 或 trace_id 作为标识
        const replayId = result.replay_id ?? result.session_id ?? traceId
        window.dispatchEvent(new CustomEvent('ep:replay-done', { detail: { traceId, replayId } }))
      },
      onError: (error) => {
        _replayAC = null
        set({ error, replayingTraceId: null, replayCurrentStep: null })
      },
    })
  },

  clearReplay: () => set({ replayResult: null, replayingTraceId: null, replaySteps: [], replayCurrentStep: null }),

  abortReplay: () => {
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
    filterWorkspaceId: '', filterProjectId: '', filterSessionId: '',
    filterDomain: '', filterKeyword: '',
    replayingTraceId: null, replayResult: null,
    replaySteps: [], replayCurrentStep: null,
    isOpen: false, loading: false, loadingMore: false, error: null,
    panelWidth: 680,
  }),
}))
