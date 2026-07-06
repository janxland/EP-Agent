'use client'

/**
 * ModelPicker — 模型选择下拉组件
 *
 * 职责：
 *   - 展示当前激活模型
 *   - 下拉菜单按分组展示所有可用模型
 *   - 选中后回调父组件
 *
 * 完全无 store 依赖，通过 props 驱动。
 */

import { useRef, useEffect } from 'react'
import type { ModelItem } from '@/shared/lib/api'

interface ModelPickerProps {
  models: ModelItem[]
  activeModelId: string
  open: boolean
  onToggle: () => void
  onClose: () => void
  onSelect: (modelId: string) => void
}

export function ModelPicker({
  models, activeModelId, open, onToggle, onClose, onSelect,
}: ModelPickerProps) {
  const menuRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    const handler = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) onClose()
    }
    const id = requestAnimationFrame(() => document.addEventListener('mousedown', handler))
    return () => { cancelAnimationFrame(id); document.removeEventListener('mousedown', handler) }
  }, [open, onClose])

  const activeModel = models.find((m) => m.id === activeModelId)

  return (
    <div className="relative" ref={menuRef}>
      <button
        onClick={onToggle}
        className="flex items-center gap-1 px-2 py-1 rounded-lg hover:bg-gray-50 transition-colors group"
        title="选择模型"
      >
        <span className="w-4 h-4 rounded-md bg-gray-800 flex items-center justify-center shrink-0">
          <span className="text-[7px] font-black text-white leading-none">EP</span>
        </span>
        <span className="text-[10px] text-gray-500 font-medium group-hover:text-gray-700 transition-colors hidden sm:inline max-w-[90px] truncate">
          {activeModel?.name ?? '默认模型'}
        </span>
        <svg className="w-2.5 h-2.5 text-gray-300 group-hover:text-gray-500 transition-colors shrink-0"
          fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {open && models.length > 0 && (
        <div className="absolute left-0 bottom-[calc(100%+4px)] z-[250] w-[220px] bg-white rounded-xl shadow-xl border border-gray-100 overflow-hidden">
          <div className="px-3 py-2 border-b border-gray-100">
            <span className="text-[10px] font-semibold text-gray-400 uppercase tracking-widest">选择模型</span>
          </div>
          <div className="max-h-64 overflow-y-auto py-1">
            {Array.from(new Set(models.map((m) => m.group))).map((group) => (
              <div key={group}>
                <div className="px-3 py-1 text-[9px] font-semibold text-gray-300 uppercase tracking-widest">
                  {group}
                </div>
                {models.filter((m) => m.group === group).map((m) => (
                  <button
                    key={m.id}
                    onClick={() => { onSelect(m.id); onClose() }}
                    className={[
                      'w-full text-left px-3 py-1.5 flex items-start gap-2 transition-colors',
                      m.id === activeModelId
                        ? 'bg-orange-50 text-orange-700'
                        : 'hover:bg-gray-50 text-gray-700',
                    ].join(' ')}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5">
                        <span className="text-[11px] font-medium truncate">{m.name}</span>
                        {m.id === activeModelId && (
                          <span className="shrink-0 w-1.5 h-1.5 rounded-full bg-orange-400" />
                        )}
                      </div>
                      <div className="text-[9px] text-gray-400 truncate mt-0.5">{m.desc}</div>
                    </div>
                  </button>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
