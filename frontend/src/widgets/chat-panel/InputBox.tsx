'use client'

/**
 * InputBox — 输入区组合组件
 *
 * 职责：
 *   - 专家角色选择按钮（顶部工具栏左侧）
 *   - 上下文用量指示器（顶部工具栏右侧）+ 清空对话
 *   - RichInput 富文本编辑器
 *   - 底部工具栏：ModelPicker + 上传状态 + 发送/停止按钮
 *   - 附件 Chip 展示
 *
 * 无 store 直接依赖，通过 props/ref 驱动。
 */

import type { RefObject, ClipboardEvent } from 'react'
import { RichInput, type FileRef } from './RichInput'
import { ModelPicker } from './ModelPicker'
import type { ModelItem } from '@/shared/lib/api'
import type { Attachment, UploadTip } from './hooks/useAttachment'
import { KIND_ICON, fmtSize } from './hooks/useAttachment'

// ─── AttachmentChip ───────────────────────────────────────────────────────────

function AttachmentChip({
  attachment, onRemove,
}: {
  attachment: Attachment
  onRemove: () => void
}) {
  return (
    <div className="flex items-center gap-1.5 px-2 py-1 bg-orange-50 border border-orange-100 rounded-lg text-xs text-orange-700 max-w-[180px]">
      <span>{KIND_ICON[attachment.kind]}</span>
      <span className="truncate flex-1">{attachment.name}</span>
      <span className="text-orange-400 font-mono text-[10px] shrink-0">{fmtSize(attachment.size)}</span>
      <button
        onClick={onRemove}
        className="shrink-0 text-orange-300 hover:text-orange-600 transition-colors ml-0.5"
        aria-label="移除附件"
      >✕</button>
    </div>
  )
}

// ─── UploadTipBadge ───────────────────────────────────────────────────────────

function UploadTipBadge({ tip }: { tip: UploadTip }) {
  return (
    <span className={[
      'text-[10px] flex items-center gap-1 transition-all',
      tip.status === 'uploading' ? 'text-orange-400' :
      tip.status === 'done'      ? 'text-green-500'  : 'text-red-400',
    ].join(' ')}>
      {tip.status === 'uploading' && (
        <svg className="w-2.5 h-2.5 animate-spin shrink-0" fill="none" viewBox="0 0 24 24">
          <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
          <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z" />
        </svg>
      )}
      {tip.status === 'done'  && <span>✓</span>}
      {tip.status === 'error' && <span>✕</span>}
      <span className="truncate max-w-[100px]">
        {tip.status === 'uploading' ? '上传中...' :
         tip.status === 'done'      ? '已引用'    : '上传失败'}
      </span>
    </span>
  )
}

// ─── InputBox ─────────────────────────────────────────────────────────────────

export interface InputBoxProps {
  // 状态
  isRunning: boolean
  sessionId: string | null
  placeholder: string
  ctxPct: number

  // 角色
  activeRoleIcon: string
  activeRoleName: string
  onOpenRolePanel: () => void

  // 附件
  attachment: Attachment | null
  onClearAttachment: () => void
  uploadTip: UploadTip | null

  // 模型
  models: ModelItem[]
  activeModelId: string
  showModelMenu: boolean
  onToggleModelMenu: () => void
  onCloseModelMenu: () => void
  onSelectModel: (id: string) => void

  // 发送 / 停止
  onSend: (text: string, fileRefs: FileRef[]) => void
  onAbort: () => void
  onClearMessages: () => void

  // RichInput refs（父组件控制插入）
  insertTextRef: RefObject<((text: string) => void) | null>
  richSendRef: RefObject<(() => void) | null>
  insertMentionRef: RefObject<((path: string, label: string, size: number) => void) | null>
  onPaste: (e: ClipboardEvent) => void
  onImageUploadStatus: (status: 'uploading' | 'done' | 'error', name?: string) => void
}

export function InputBox({
  isRunning, sessionId, placeholder, ctxPct,
  activeRoleIcon, activeRoleName, onOpenRolePanel,
  attachment, onClearAttachment, uploadTip,
  models, activeModelId, showModelMenu, onToggleModelMenu, onCloseModelMenu, onSelectModel,
  onSend, onAbort, onClearMessages,
  insertTextRef, richSendRef, insertMentionRef, onPaste, onImageUploadStatus,
}: InputBoxProps) {
  const ctxColor = ctxPct >= 80 ? 'text-red-500 bg-red-50 border-red-100'
                 : ctxPct >= 50 ? 'text-orange-500 bg-orange-50 border-orange-100'
                 : 'text-gray-400 bg-gray-50 border-gray-100'

  return (
    <div className="px-3 pb-3 shrink-0">
      <div className={[
        'rounded-2xl border transition-all duration-200 shadow-sm',
        isRunning
          ? 'border-gray-100 bg-gray-50/80 opacity-80'
          : 'border-gray-200 bg-white focus-within:border-orange-300 focus-within:shadow-md focus-within:shadow-orange-50/60',
      ].join(' ')}>

        {/* ── 顶部工具栏 ── */}
        <div className="flex items-center justify-between px-3 pt-2.5 pb-1">
          {/* 左：专家角色 */}
          <button
            onClick={onOpenRolePanel}
            disabled={isRunning}
            className="flex items-center gap-1.5 px-2 py-1 rounded-lg hover:bg-gray-50 transition-colors group disabled:opacity-50 disabled:cursor-not-allowed"
            title="切换专家角色"
          >
            <span className="text-base leading-none">{activeRoleIcon}</span>
            <span className="text-[11px] font-semibold text-gray-700 max-w-[80px] truncate group-hover:text-orange-600 transition-colors">
              {activeRoleName}
            </span>
            <svg className="w-3 h-3 text-gray-300 group-hover:text-orange-400 transition-colors shrink-0"
              fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>

          {/* 右：上下文用量 + 清空 */}
          <button
            onClick={onClearMessages}
            title={`上下文已用约 ${ctxPct}%，点击清空对话`}
            className={`flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-bold transition-all hover:scale-105 ${ctxColor}`}
          >
            <span className="tabular-nums">{ctxPct}%</span>
            <svg className="w-2.5 h-2.5 opacity-60" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15" />
            </svg>
          </button>
        </div>

        {/* ── 附件 Chip ── */}
        {attachment && (
          <div className="px-3 pb-1">
            <AttachmentChip attachment={attachment} onRemove={onClearAttachment} />
          </div>
        )}

        {/* ── 富文本编辑器 ── */}
        <div className="px-3 pb-1">
          <RichInput
            disabled={isRunning || !sessionId}
            placeholder={placeholder}
            onSend={onSend}
            onPaste={onPaste as unknown as (e: ClipboardEvent) => void}
            insertTextRef={insertTextRef}
            sendRef={richSendRef}
            insertMentionRef={insertMentionRef}
            onImageUploadStatus={onImageUploadStatus}
          />
        </div>

        {/* ── 底部工具栏 ── */}
        <div className="flex items-center justify-between px-2.5 pb-2.5 pt-1">
          {/* 左：模型选择 + 上传状态 */}
          <div className="flex items-center gap-1.5">
            <ModelPicker
              models={models}
              activeModelId={activeModelId}
              open={showModelMenu}
              onToggle={onToggleModelMenu}
              onClose={onCloseModelMenu}
              onSelect={onSelectModel}
            />
            {uploadTip && <UploadTipBadge tip={uploadTip} />}
          </div>

          {/* 右：发送 / 停止 */}
          <div className="flex items-center gap-1.5">
            {isRunning ? (
              <button
                onClick={onAbort}
                title="停止（中断当前对话）"
                className="w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-150 bg-red-500 text-white hover:bg-red-600 active:scale-90 shadow-sm shadow-red-200"
              >
                <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 24 24">
                  <rect x="5" y="5" width="14" height="14" rx="2" />
                </svg>
              </button>
            ) : (
              <button
                onClick={() => richSendRef.current?.()}
                disabled={!sessionId}
                className={[
                  'w-7 h-7 rounded-lg flex items-center justify-center transition-all duration-150',
                  !sessionId
                    ? 'bg-gray-100 text-gray-300 cursor-not-allowed'
                    : 'bg-orange-500 text-white hover:bg-orange-600 active:scale-90 shadow-sm shadow-orange-200',
                ].join(' ')}
                title="发送（Enter）"
              >
                <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2.5} d="M5 12h14M12 5l7 7-7 7" />
                </svg>
              </button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
