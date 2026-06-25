'use client'

/**
 * ConfirmDialog — 通用确认弹窗（替代 window.confirm）
 * - Portal 渲染到 body，不受父级 overflow/z-index 影响
 * - Esc 取消 / Enter 确认
 * - 点击遮罩取消
 */

import { useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import type { ReactNode } from 'react'

export interface ConfirmDialogProps {
  title: string
  description: ReactNode
  confirmText?: string
  cancelText?: string
  variant?: 'danger' | 'warning'
  meta?: string
  onConfirm: () => void | Promise<void>
  onCancel: () => void
}

export function ConfirmDialog({
  title,
  description,
  confirmText = '确定',
  cancelText = '取消',
  variant = 'danger',
  meta,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const overlayRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); onCancel() }
      if (e.key === 'Enter')  { e.preventDefault(); e.stopPropagation(); void onConfirm() }
    }
    // capture=true 确保优先于其他 keydown 处理
    document.addEventListener('keydown', handler, true)
    return () => document.removeEventListener('keydown', handler, true)
  }, [onConfirm, onCancel])

  const isDanger  = variant === 'danger'
  const iconPath  = isDanger
    ? 'M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16'
    : 'M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z'
  const iconBg    = isDanger ? 'bg-red-50'   : 'bg-orange-50'
  const iconColor = isDanger ? 'text-red-500' : 'text-orange-500'
  const btnColor  = isDanger
    ? 'text-red-500 hover:text-red-600 hover:bg-red-50'
    : 'text-orange-500 hover:text-orange-600 hover:bg-orange-50'

  if (typeof document === 'undefined') return null

  return createPortal(
    <div
      ref={overlayRef}
      className="fixed inset-0 z-[9000] flex items-center justify-center bg-black/25 backdrop-blur-[2px]"
      onMouseDown={(e) => { if (e.target === overlayRef.current) onCancel() }}
    >
      <div
        className="bg-white rounded-2xl shadow-2xl border border-gray-100 w-72 overflow-hidden"
        onMouseDown={(e) => e.stopPropagation()}
      >
        <div className="px-5 pt-5 pb-4">
          <div className="flex items-start gap-3">
            <div className={`shrink-0 w-9 h-9 rounded-full ${iconBg} flex items-center justify-center mt-0.5`}>
              <svg className={`w-[18px] h-[18px] ${iconColor}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d={iconPath} />
              </svg>
            </div>
            <div className="flex-1 min-w-0">
              <h3 className="text-sm font-semibold text-gray-800 leading-snug">{title}</h3>
              <div className="mt-1 text-xs text-gray-500 leading-relaxed">{description}</div>
              {meta && <p className="mt-1 text-[10px] text-gray-400 font-mono">{meta}</p>}
            </div>
          </div>
        </div>
        <div className="flex border-t border-gray-100">
          <button
            onClick={onCancel}
            className="flex-1 py-3 text-sm text-gray-500 hover:text-gray-700 hover:bg-gray-50 transition-colors font-medium border-r border-gray-100"
          >
            {cancelText}
          </button>
          <button
            onClick={() => void onConfirm()}
            className={`flex-1 py-3 text-sm transition-colors font-semibold ${btnColor}`}
          >
            {confirmText}
          </button>
        </div>
      </div>
    </div>,
    document.body
  )
}
