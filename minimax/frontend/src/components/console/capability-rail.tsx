'use client'

import { Boxes } from 'lucide-react'
import { capabilities } from '@/shared/constants/capabilities'
import { useConsoleStore } from '@/state/console-store'

export function CapabilityRail() {
  const capability = useConsoleStore((state) => state.capability)
  const setCapability = useConsoleStore((state) => state.setCapability)

  return (
    <aside className="capability-rail border-r border-zinc-200 bg-zinc-950 text-white">
      <div className="flex h-14 items-center justify-center border-b border-white/10"><span className="grid h-9 w-9 place-items-center rounded-xl bg-orange-500 shadow-lg shadow-orange-950/30"><Boxes className="h-5 w-5" /></span></div>
      <nav className="flex flex-1 flex-col gap-1.5 overflow-y-auto p-2" aria-label="模型能力">
        {capabilities.map((item) => {
          const Icon = item.icon
          const active = capability === item.id
          return (
            <button key={item.id} type="button" onClick={() => setCapability(item.id)} title={`${item.label} · ${item.description}`} className={`group flex min-h-14 flex-col items-center justify-center gap-1 rounded-xl px-1 text-[10px] font-medium transition ${active ? 'bg-orange-500 text-white' : 'text-zinc-400 hover:bg-white/10 hover:text-white'}`}>
              <Icon className="h-[18px] w-[18px]" strokeWidth={1.8} />
              <span>{item.shortLabel}</span>
            </button>
          )
        })}
      </nav>
    </aside>
  )
}
