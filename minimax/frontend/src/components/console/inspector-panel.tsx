'use client'

import { Activity, Circle, ShieldCheck, X } from 'lucide-react'
import { capabilityById } from '@/shared/constants/capabilities'
import { useConsoleStore } from '@/state/console-store'

const statusLabel = { running: '运行中', succeeded: '已完成', failed: '失败', cancelled: '已取消' }

export function InspectorPanel({ gatewayUrl }: { gatewayUrl: string }) {
  const capability = useConsoleStore((state) => state.capability)
  const runs = useConsoleStore((state) => state.runs)
  const inspectorOpen = useConsoleStore((state) => state.inspectorOpen)
  const toggleInspector = useConsoleStore((state) => state.toggleInspector)

  return (
    <aside className={`inspector-panel border-l border-zinc-200 bg-white ${inspectorOpen ? 'mobile-panel-open' : ''}`}>
      <header className="flex h-14 items-center justify-between border-b border-zinc-100 px-4">
        <div><p className="text-[10px] font-semibold uppercase tracking-[.18em] text-zinc-400">Inspector</p><h2 className="text-sm font-semibold text-zinc-900">运行检查器</h2></div>
        <button type="button" onClick={toggleInspector} className="mobile-only rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100" aria-label="关闭检查器"><X className="h-4 w-4" /></button>
      </header>
      <div className="space-y-5 overflow-y-auto p-4">
        <section className="rounded-2xl border border-zinc-200 p-4">
          <p className="text-xs font-semibold uppercase tracking-wider text-zinc-400">Active capability</p>
          <p className="mt-2 font-semibold text-zinc-900">{capabilityById[capability].label}</p>
          <p className="mt-1 text-xs leading-5 text-zinc-500">{capabilityById[capability].description}</p>
        </section>
        <section>
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-800"><ShieldCheck className="h-4 w-4 text-emerald-600" />安全边界</div>
          <div className={`rounded-xl border px-3 py-2.5 text-xs leading-5 ${gatewayUrl ? 'border-emerald-200 bg-emerald-50 text-emerald-800' : 'border-orange-200 bg-orange-50 text-orange-800'}`}>{gatewayUrl ? `已配置网关：${gatewayUrl}` : '未配置网关；不会发起任何官方 API 请求。'}</div>
        </section>
        <section>
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-800"><Activity className="h-4 w-4 text-orange-500" />本次会话运行</div>
          <div className="space-y-2">
            {runs.length === 0 ? <p className="rounded-xl border border-dashed border-zinc-200 p-4 text-center text-xs text-zinc-400">还没有运行记录</p> : runs.map((run) => <div key={run.id} className="rounded-xl border border-zinc-200 p-3"><div className="flex items-center gap-2"><Circle className={`h-2.5 w-2.5 fill-current ${run.status === 'running' ? 'animate-pulse text-orange-500' : run.status === 'succeeded' ? 'text-emerald-500' : 'text-red-500'}`} /><span className="min-w-0 flex-1 truncate text-xs font-medium text-zinc-700">{run.label}</span><span className="text-[10px] text-zinc-400">{statusLabel[run.status]}</span></div></div>)}
          </div>
        </section>
      </div>
    </aside>
  )
}
