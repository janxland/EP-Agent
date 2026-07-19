'use client'

import { BookOpen, ChevronRight, Clock3, FileStack, Layers3, X } from 'lucide-react'
import { capabilities, capabilityById } from '@/shared/constants/capabilities'
import { useConsoleStore } from '@/state/console-store'

export function ResourcePanel() {
  const capability = useConsoleStore((state) => state.capability)
  const resourceOpen = useConsoleStore((state) => state.resourceOpen)
  const toggleResource = useConsoleStore((state) => state.toggleResource)
  const setCapability = useConsoleStore((state) => state.setCapability)
  const current = capabilityById[capability]

  return (
    <aside className={`resource-panel border-r border-zinc-200 bg-white ${resourceOpen ? 'mobile-panel-open' : ''}`}>
      <header className="flex h-14 items-center justify-between border-b border-zinc-100 px-4">
        <div><p className="text-[10px] font-semibold uppercase tracking-[.18em] text-zinc-400">Explorer</p><h2 className="text-sm font-semibold text-zinc-900">模型资源</h2></div>
        <button type="button" onClick={toggleResource} className="mobile-only rounded-lg p-1.5 text-zinc-400 hover:bg-zinc-100" aria-label="关闭资源栏"><X className="h-4 w-4" /></button>
      </header>
      <div className="space-y-5 overflow-y-auto p-3">
        <section>
          <div className="mb-2 flex items-center gap-2 px-2 text-xs font-semibold uppercase tracking-wider text-zinc-400"><Layers3 className="h-3.5 w-3.5" /> Capabilities</div>
          <div className="space-y-1">
            {capabilities.map((item) => {
              const Icon = item.icon
              return <button key={item.id} type="button" onClick={() => setCapability(item.id)} className={`flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-sm transition ${capability === item.id ? 'bg-orange-50 font-medium text-orange-700' : 'text-zinc-600 hover:bg-zinc-50'}`}><Icon className="h-4 w-4" /><span className="flex-1">{item.label}</span><ChevronRight className="h-3.5 w-3.5 opacity-40" /></button>
            })}
          </div>
        </section>
        <section className="rounded-2xl border border-zinc-200 bg-zinc-50 p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-zinc-800"><BookOpen className="h-4 w-4 text-orange-500" />当前工作区</div>
          <p className="mt-2 text-sm font-medium text-zinc-900">{current.label}</p>
          <p className="mt-1 text-xs leading-5 text-zinc-500">{current.description}</p>
        </section>
        <section className="space-y-2 text-xs text-zinc-500">
          <div className="flex items-center gap-2 rounded-lg px-2 py-1.5"><FileStack className="h-3.5 w-3.5" />文件由网关托管</div>
          <div className="flex items-center gap-2 rounded-lg px-2 py-1.5"><Clock3 className="h-3.5 w-3.5" />任务状态实时查询</div>
        </section>
      </div>
    </aside>
  )
}
