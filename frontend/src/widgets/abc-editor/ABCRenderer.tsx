'use client'

import { useEffect, useRef, useState } from 'react'

interface ABCRendererProps {
  abc: string
  title?: string
  className?: string
}

/**
 * 校验 ABC 字符串是否具备最基本的可渲染结构。
 * abcjs 要求至少有 X:（序号）和 K:（调号）两个 Header 字段，
 * 缺少任意一个都会导致 abcjs 内部 split/undefined 崩溃。
 */
function isValidABC(abc: string): boolean {
  if (!abc || typeof abc !== 'string') return false
  const trimmed = abc.trim()
  return trimmed.includes('X:') && trimmed.includes('K:')
}

// 全局唯一 ID 计数器，给播放器容器生成唯一 CSS 选择器
let _synthIdCounter = 0

// 全局只注入一次 abcjs-audio.css（SynthController 强依赖）
let _audioCssInjected = false
function ensureAudioCss() {
  if (_audioCssInjected) return
  if (document.getElementById('abcjs-audio-css')) { _audioCssInjected = true; return }
  const link = document.createElement('link')
  link.id   = 'abcjs-audio-css'
  link.rel  = 'stylesheet'
  link.href = 'https://cdn.jsdelivr.net/npm/abcjs@6.4.4/abcjs-audio.css'
  document.head.appendChild(link)
  _audioCssInjected = true
}

/**
 * ABCRenderer — 使用 abcjs 在浏览器端渲染 ABC 乐谱，并提供音频播放功能
 *
 * 防御性设计：
 *   - 渲染前校验 ABC 格式，避免 abcjs 内部 undefined.split 崩溃
 *   - 动态 import abcjs（避免 SSR 报错）
 *   - 使用 abcjs synth.SynthController 渲染播放控件（播放/暂停/进度/速度）
 *   - SynthController.load() 接受 CSS 选择器，用唯一 id 避免冲突
 *   - 所有异常统一 catch，降级为友好错误提示
 */
export function ABCRenderer({ abc, title, className }: ABCRendererProps) {
  const containerRef  = useRef<HTMLDivElement>(null)
  // 每个组件实例持有一个固定的唯一 id，用于 SynthController.load(selector)
  // 用 useState 惰性初始化，避免 StrictMode 双重 mount 导致计数器错位
  const [synthId]     = useState(() => `abcjs-synth-${++_synthIdCounter}`)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const synthCtrlRef  = useRef<any>(null)

  const [error, setError]         = useState<string | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [synthReady, setSynthReady] = useState(false)
  const [noAudio, setNoAudio]     = useState(false)

  useEffect(() => {
    if (!abc || !containerRef.current) return
    if (!isValidABC(abc)) {
      setError('ABC 格式不完整（缺少 X: 或 K: 字段），等待 AI 返回完整谱子...')
      return
    }

    setError(null)
    setIsLoading(true)
    setSynthReady(false)
    setNoAudio(false)

    // 销毁旧播放器
    if (synthCtrlRef.current) {
      try { synthCtrlRef.current.destroy?.() } catch { /* ignore */ }
      synthCtrlRef.current = null
    }

    import('abcjs').then((abcjs) => {
      if (!containerRef.current) return

      try {
        // ── 1. 渲染乐谱 ────────────────────────────────────────────────────────
        const visualObj = abcjs.renderAbc(containerRef.current, abc, {
          responsive: 'resize',
          add_classes: true,
          paddingtop: 16,
          paddingbottom: 16,
          paddingright: 16,
          paddingleft: 16,
          staffwidth: Math.max(300, (containerRef.current.clientWidth || 600) - 32),
          scale: 1.1,
        })

        setIsLoading(false)

        // ── 2. 初始化播放器 ────────────────────────────────────────────────────
        // SynthController 强依赖 abcjs-audio.css，必须先注入
        ensureAudioCss()

        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        const synthModule = (abcjs as any).synth
        if (!synthModule || !synthModule.supportsAudio()) {
          setNoAudio(true)
          return
        }

        if (!visualObj?.[0]) return

        // SynthController.load() 需要 CSS 选择器，且元素必须已挂载到 DOM
        const selector = `#${synthId}`
        const ctrl = new synthModule.SynthController()
        synthCtrlRef.current = ctrl

        ctrl.load(selector, null, {
          displayLoop:     true,
          displayRestart:  true,
          displayPlay:     true,
          displayProgress: true,
          displayWarp:     true,
        })

        // setTune(visualObj, userAction=false, audioParams)
        // userAction=false：不立即创建 AudioContext，等用户点播放时再创建
        ctrl.setTune(visualObj[0], false, {})
          .then(() => {
            setSynthReady(true)
          })
          .catch(() => {
            // soundfont 加载失败等，不影响乐谱显示
            setNoAudio(true)
          })

      } catch (e) {
        setIsLoading(false)
        setError(e instanceof Error ? e.message : 'ABC 渲染失败，请检查谱子格式')
      }
    }).catch(() => {
      setIsLoading(false)
      setError('abcjs 加载失败，请检查网络或刷新页面')
    })

    return () => {
      if (synthCtrlRef.current) {
        try { synthCtrlRef.current.destroy?.() } catch { /* ignore */ }
        synthCtrlRef.current = null
      }
    }
  }, [abc])

  // ── 无内容 ──────────────────────────────────────────────────────────────────
  if (!abc) {
    return (
      <div className={`flex items-center justify-center h-48 text-gray-400 ${className ?? ''}`}>
        <div className="text-center space-y-2">
          <div className="text-4xl">🎵</div>
          <p className="text-sm">上传 JSON 谱子后，乐谱将在此显示</p>
        </div>
      </div>
    )
  }

  // ── 渲染错误 ─────────────────────────────────────────────────────────────────
  if (error) {
    return (
      <div className={`flex items-center justify-center min-h-24 p-4 ${className ?? ''}`}>
        <div className="flex items-start gap-2 text-amber-600 bg-amber-50 border border-amber-100 rounded-xl px-4 py-3 text-xs max-w-sm">
          <span className="shrink-0 mt-0.5">⚠️</span>
          <span>{error}</span>
        </div>
      </div>
    )
  }

  return (
    <div className={className}>
      {title && (
        <p className="text-xs text-gray-500 mb-1 px-4">{title}</p>
      )}

      {/* Loading 骨架屏 */}
      {isLoading && (
        <div className="w-full px-4 py-6 space-y-3 animate-pulse">
          {[0, 1, 2, 3, 4].map((i) => (
            <div key={i} className="flex items-center gap-2">
              <div className="w-3 h-2 bg-gray-200 rounded" />
              <div className="flex-1 h-px bg-gray-200" />
            </div>
          ))}
          <div className="flex items-end gap-3 px-2 pt-2">
            {[32, 24, 40, 28, 36, 20, 44, 30, 38, 26].map((h, i) => (
              <div key={i} className="w-2.5 rounded-full bg-gray-200" style={{ height: h }} />
            ))}
          </div>
          <p className="text-[10px] text-gray-300 text-center pt-1">乐谱渲染中...</p>
        </div>
      )}

      {/* 乐谱渲染容器 */}
      <div
        ref={containerRef}
        className={[
          'w-full overflow-x-auto transition-opacity duration-300',
          isLoading ? 'opacity-0 h-0 overflow-hidden' : 'opacity-100',
        ].join(' ')}
      />

      {/* 播放器容器：abcjs SynthController 将控件注入此 div
          - 始终挂载在 DOM（SynthController.load() 需要在 useEffect 里找到它）
          - noAudio 时用 style 隐藏，不做条件渲染 */}
      <div
        id={synthId}
        className="px-4 pb-4"
        style={{ display: noAudio ? 'none' : undefined }}
      />

      {/* 音频不可用时的友好提示 */}
      {!isLoading && noAudio && (
        <p className="px-4 pb-3 text-[10px] text-gray-300 text-center">
          🔇 音源加载失败或当前环境不支持音频播放
        </p>
      )}
    </div>
  )
}
