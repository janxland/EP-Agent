'use client'

import { X } from 'lucide-react'
import { capabilityById } from '@/shared/constants/capabilities'
import { useConsoleStore } from '@/state/console-store'

export function WorkbenchTabs() {
  const tabs = useConsoleStore((state) => state.tabs)
  const activeTabId = useConsoleStore((state) => state.activeTabId)
  const setActiveTab = useConsoleStore((state) => state.setActiveTab)
  const closeTab = useConsoleStore((state) => state.closeTab)

  return <div className="flex h-11 overflow-x-auto border-b border-zinc-200 bg-zinc-50/80">{tabs.map((tab) => { const Icon = capabilityById[tab.capability].icon; const active = tab.id === activeTabId; return <div key={tab.id} className={`group flex min-w-36 items-center gap-2 border-r border-zinc-200 px-3 text-xs ${active ? 'border-t-2 border-t-orange-500 bg-white text-zinc-900' : 'text-zinc-500 hover:bg-white/70'}`}><button type="button" onClick={() => setActiveTab(tab.id)} className="flex min-w-0 flex-1 items-center gap-2"><Icon className="h-3.5 w-3.5 shrink-0" /><span className="truncate">{tab.title}</span></button>{tabs.length > 1 ? <button type="button" onClick={() => closeTab(tab.id)} className="rounded p-0.5 opacity-0 hover:bg-zinc-200 group-hover:opacity-100" aria-label={`关闭 ${tab.title}`}><X className="h-3 w-3" /></button> : null}</div>})}</div>
}
