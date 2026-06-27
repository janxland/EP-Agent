'use client'

/**
 * PreviewTabs.tsx — 浏览器标签页式预览组件
 *
 * 设计原则：
 *   - 纯展示层：所有状态来自 preview-tabs.store，组件本身无 local state
 *   - 完全解耦：不直接依赖 abcNotation / previewFile，只读 store
 *   - 可插拔：替换 page.tsx 中央区域即可，无其他侵入
 *   - 标签栏：Chrome 风格，可关闭，支持溢出滚动
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { usePreviewTabsStore, type PreviewTab, type FileTab, type AbcTab } from '../store/preview-tabs.store'
import { getFileRawUrl, getFileDownloadUrl, IMAGE_EXTS } from '@/shared/lib/workspace-files-api'
import { ABCRenderer } from '@/widgets/abc-editor/ABCRenderer'
import { PipelineStatus } from '@/widgets/pipeline-status/PipelineStatus'
import { ExportPanel } from '@/widgets/export-panel/ExportPanel'

// ─── 文件类型工具 ────────────────────────────────────────────────────────────────

const TEXT_EXTS = new Set(['abc','txt','md','json','html','htm','css','js','ts','tsx','jsx','xml','yaml','yml','csv','svg','py','go','sh'])

function fileIcon(ext: string) {
  if (ext === 'html' || ext === 'htm') return '🌐'
  if (IMAGE_EXTS.has(ext)) return '🖼️'
  if (ext === 'mid' || ext === 'midi') return '🎹'
  if (ext === 'mp3' || ext === 'wav' || ext === 'ogg') return '🎵'
  if (TEXT_EXTS.has(ext)) return '📄'
  return '📎'
}

function tabIcon(tab: PreviewTab) {
  if (tab.type === 'abc')    return '🎼'
  if (tab.type === 'logs')   return '📋'
  if (tab.type === 'export') return '💾'
  if (tab.type === 'file')   return fileIcon((tab as FileTab).file.ext)
  return '📄'
}

// ─── 标签页关闭按钮 ──────────────────────────────────────────────────────────────

function CloseBtn({ onClose }: { onClose: (e: React.MouseEvent) => void }) {
  return (
    <span
      onClick={onClose}
      className="ml-1 w-4 h-4 flex items-center justify-center rounded-sm text-[10px]
                 text-gray-300 hover:text-gray-600 hover:bg-gray-200/80
                 opacity-0 group-hover/tab:opacity-100 transition-all shrink-0"
    >
      ✕
    </span>
  )
}

// ─── 单个标签 ────────────────────────────────────────────────────────────────────

function TabItem({ tab, isActive }: { tab: PreviewTab; isActive: boolean }) {
  const { activateTab, closeTab } = usePreviewTabsStore()

  const handleClose = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    closeTab(tab.id)
  }, [tab.id, closeTab])

  return (
    <div
      onClick={() => activateTab(tab.id)}
      className={[
        'group/tab relative flex items-center gap-1.5 px-3 py-0 h-9 shrink-0',
        'cursor-pointer select-none transition-all duration-150',
        'border-r border-gray-100 text-xs font-medium max-w-[160px]',
        isActive
          ? 'bg-white text-gray-700 border-b-2 border-b-orange-500 -mb-px z-10'
          : 'bg-gray-50/80 text-gray-400 hover:bg-white/70 hover:text-gray-600 border-b-2 border-b-transparent',
      ].join(' ')}
    >
      <span className="text-[13px] shrink-0">{tabIcon(tab)}</span>
      <span className="truncate min-w-0">{tab.title}</span>
      <CloseBtn onClose={handleClose} />
      {/* 激活指示器 */}
      {isActive && (
        <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-orange-500 rounded-t-full" />
      )}
    </div>
  )
}

// ─── 标签栏（含溢出横向滚动）────────────────────────────────────────────────────

function TabBar() {
  const { tabs, activeTabId, openTab } = usePreviewTabsStore()
  const scrollRef = useRef<HTMLDivElement>(null)

  // 鼠标滚轮横向滚动
  useEffect(() => {
    const el = scrollRef.current
    if (!el) return
    const onWheel = (e: WheelEvent) => {
      if (e.deltaY !== 0) { e.preventDefault(); el.scrollLeft += e.deltaY }
    }
    el.addEventListener('wheel', onWheel, { passive: false })
    return () => el.removeEventListener('wheel', onWheel)
  }, [])

  return (
    <div className="flex items-end border-b border-gray-100 bg-gray-50/50 shrink-0 h-9">
      {/* 标签列表（横向滚动） */}
      <div ref={scrollRef} className="flex items-end flex-1 overflow-x-auto scrollbar-none min-w-0">
        {tabs.map(tab => (
          <TabItem key={tab.id} tab={tab} isActive={tab.id === activeTabId} />
        ))}
      </div>

      {/* 新增标签按钮（快捷入口） */}
      <div className="flex items-center gap-0.5 px-2 shrink-0 border-l border-gray-100 h-full">
        <button
          onClick={() => openTab({ type: 'logs', title: '执行日志' })}
          title="打开执行日志"
          className="w-6 h-6 flex items-center justify-center rounded text-gray-300 hover:text-gray-500 hover:bg-gray-100 transition-colors text-[11px]"
        >📋</button>
        <button
          onClick={() => openTab({ type: 'export', title: '导出' })}
          title="打开导出面板"
          className="w-6 h-6 flex items-center justify-center rounded text-gray-300 hover:text-gray-500 hover:bg-gray-100 transition-colors text-[11px]"
        >💾</button>
      </div>
    </div>
  )
}

// ─── 文件内容渲染 ────────────────────────────────────────────────────────────────

function FileContent({ tab }: { tab: FileTab }) {
  const [text, setText] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const { file, workspaceId, projectId } = tab
  // projectId 存在时走三层路径 workspace/{ws}/projects/{proj}/，否则退回两层（向后兼容）
  const rawUrl = getFileRawUrl(workspaceId, file.path, projectId)
  const dlUrl  = getFileDownloadUrl(workspaceId, file.path, projectId)
  const isImg  = IMAGE_EXTS.has(file.ext)
  const isHtml = file.ext === 'html' || file.ext === 'htm'
  const isText = TEXT_EXTS.has(file.ext) && !isHtml
  const isMidi = file.ext === 'mid' || file.ext === 'midi'
  const isAudio = ['mp3','wav','ogg','m4a'].includes(file.ext)

  useEffect(() => {
    if (!isText) return
    setLoading(true)
    setText(null)
    fetch(rawUrl)
      .then(r => r.text())
      .then(t => setText(t))
      .catch(() => setText('无法加载文件内容'))
      .finally(() => setLoading(false))
  }, [rawUrl, isText])

  if (isHtml) return (
    <iframe
      src={rawUrl}
      className="w-full h-full border-0"
      title={file.name}
      sandbox="allow-scripts allow-same-origin allow-forms"
    />
  )

  if (isImg) return (
    <div className="flex items-center justify-center h-full p-8 bg-[#f8f8f8]"
      style={{ backgroundImage: 'radial-gradient(circle, #e5e7eb 1px, transparent 1px)', backgroundSize: '20px 20px' }}>
      <img src={rawUrl} alt={file.name}
        className="max-w-full max-h-full object-contain rounded-xl shadow-lg" />
    </div>
  )

  if (isAudio) return (
    <div className="flex flex-col items-center justify-center h-full gap-5 p-8">
      <div className="w-20 h-20 bg-gradient-to-br from-orange-50 to-orange-100 rounded-2xl flex items-center justify-center text-4xl shadow-inner">🎵</div>
      <p className="text-sm font-medium text-gray-700">{file.name}</p>
      <audio controls src={rawUrl} className="w-full max-w-sm rounded-lg" />
    </div>
  )

  if (isMidi) return (
    <div className="flex flex-col items-center justify-center h-full gap-5 p-8">
      <div className="w-20 h-20 bg-gradient-to-br from-violet-50 to-violet-100 rounded-2xl flex items-center justify-center text-4xl shadow-inner">🎹</div>
      <p className="text-sm font-medium text-gray-700">{file.name}</p>
      <p className="text-xs text-gray-400">MIDI 文件</p>
      <a href={dlUrl} download={file.name}
        className="text-xs px-4 py-2 rounded-lg bg-violet-50 text-violet-600 hover:bg-violet-100 transition-colors border border-violet-100 font-medium">
        下载 MIDI
      </a>
    </div>
  )

  if (isText) return (
    loading
      ? <div className="flex items-center justify-center h-full"><span className="text-xs text-gray-400 animate-pulse">加载中…</span></div>
      : <pre className="p-5 text-[11.5px] font-mono text-gray-700 leading-relaxed whitespace-pre-wrap break-all overflow-auto h-full">{text}</pre>
  )

  // 二进制 fallback
  return (
    <div className="flex flex-col items-center justify-center h-full gap-4 text-center p-8">
      <span className="text-5xl opacity-20">📎</span>
      <p className="text-sm text-gray-500 font-medium">{file.name}</p>
      <p className="text-xs text-gray-400">二进制文件，请下载后查看</p>
      <a href={dlUrl} download={file.name}
        className="text-xs px-4 py-2 rounded-lg bg-orange-50 text-orange-500 hover:bg-orange-100 transition-colors border border-orange-100 font-medium">
        下载文件
      </a>
    </div>
  )
}

// ─── ABC 内容渲染 ────────────────────────────────────────────────────────────────

function AbcContent({ tab }: { tab: AbcTab }) {
  return (
    <div className="p-5 overflow-auto h-full">
      <details className="mb-4 group">
        <summary className="text-xs text-gray-400 cursor-pointer hover:text-gray-600 select-none flex items-center gap-1.5 list-none">
          <svg className="w-2.5 h-2.5 transition-transform group-open:rotate-90 text-gray-300"
            fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 5l7 7-7 7" />
          </svg>
          <span>ABC 源码</span>
        </summary>
        <pre className="mt-2 p-3 bg-gray-50 rounded-xl text-xs text-gray-600 overflow-x-auto whitespace-pre-wrap font-mono border border-gray-100 max-h-40 overflow-y-auto">
          {tab.abc}
        </pre>
      </details>
      <div className="border border-gray-100 rounded-2xl overflow-hidden shadow-sm">
        <ABCRenderer abc={tab.abc} title={tab.scoreTitle} />
      </div>
    </div>
  )
}

// ─── 空状态（无标签时） ──────────────────────────────────────────────────────────

function EmptyState() {
  return (
    <div className="flex flex-col items-center justify-center h-full text-center p-10 space-y-4">
      <div className="w-16 h-16 bg-gradient-to-br from-gray-50 to-gray-100 rounded-2xl flex items-center justify-center text-4xl shadow-inner">🎼</div>
      <div className="space-y-1.5">
        <p className="text-sm font-semibold text-gray-600">在右侧对话框开始</p>
        <p className="text-xs text-gray-400 max-w-xs leading-relaxed">AI 生成乐谱或点击文件树中的文件，将在此处以标签页方式预览</p>
      </div>
      <div className="flex flex-col gap-2 text-xs text-gray-400 max-w-xs">
        {[
          ['💬', '直接说话', '「生成中国风配乐」「升高八度」'],
          ['🗂️', '点击文件', '文件树中点击任意文件即可预览'],
          ['🎵', '附加音频', '粘贴 MP3 克隆你的声音'],
        ].map(([icon, t, desc]) => (
          <div key={t} className="flex items-start gap-2 px-3 py-2 bg-gray-50 rounded-xl border border-gray-100 text-left">
            <span className="text-base shrink-0">{icon}</span>
            <div><p className="font-medium text-gray-600">{t}</p><p className="text-gray-400 text-[11px]">{desc}</p></div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── 内容区（根据激活标签渲染对应内容）──────────────────────────────────────────

function TabContent() {
  const { tabs, activeTabId } = usePreviewTabsStore()
  const activeTab = tabs.find(t => t.id === activeTabId)

  if (!activeTab) return <EmptyState />

  // 所有标签内容都渲染，只是 display none（保留 iframe/audio 状态）
  return (
    <div className="flex-1 overflow-hidden relative">
      {tabs.map(tab => (
        <div
          key={tab.id}
          className="absolute inset-0 overflow-auto"
          style={{ display: tab.id === activeTabId ? 'block' : 'none' }}
        >
          {tab.type === 'file'   && <FileContent   tab={tab as FileTab} />}
          {tab.type === 'abc'    && <AbcContent    tab={tab as AbcTab} />}
          {tab.type === 'logs'   && <div className="p-4"><PipelineStatus /></div>}
          {tab.type === 'export' && <ExportPanel />}
        </div>
      ))}
    </div>
  )
}

// ─── 主组件（对外暴露的唯一接口）────────────────────────────────────────────────

export function PreviewTabs() {
  const { tabs } = usePreviewTabsStore()

  return (
    <div className="flex flex-col h-full overflow-hidden">
      <TabBar />
      {tabs.length === 0
        ? <EmptyState />
        : <TabContent />
      }
    </div>
  )
}
