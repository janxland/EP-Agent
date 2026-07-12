'use client'

// 允许导入第三方 CSS，避免 abcjs-audio.css 等资源触发 TS 检查
declare module '*.css'
declare module 'abcjs/*'

// abcjs audio 控件依赖这个样式文件，必须在组件侧导入，否则会报 CSS required
// @ts-ignore
import 'abcjs/abcjs-audio.css'
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
  const synthIdRef    = useRef(`abcjs-synth-${++_synthIdCounter}`)
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

    let cancelled = false

    import('abcjs').then((abcjs: any) => {
      if (cancelled || !containerRef.current) return

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
        const synthModule = (abcjs as any).synth
        if (!synthModule || !synthModule.supportsAudio()) {
          setNoAudio(true)
          return
        }

        if (!visualObj?.[0]) {
          setNoAudio(true)
          return
        }

        // 保证播放器容器在 DOM 中且可被 abcjs 测量
        const selector = `#${synthIdRef.current}`
        const ctrl = new synthModule.SynthController()
        synthCtrlRef.current = ctrl

        // 延迟一帧，确保 React 已完成布局更新
        const raf = requestAnimationFrame(() => {
          if (cancelled) return

          try {
            ctrl.load(selector, null, {
              displayLoop:     true,
              displayRestart:  true,
              displayPlay:     true,
              displayProgress: true,
              displayWarp:     true,
            })

            const createSynth = new synthModule.CreateSynth()
            createSynth.init({ visualObj: visualObj[0] })
              .then(() => ctrl.setTune(visualObj[0], false, {}))
              .then(() => {
                if (!cancelled) setSynthReady(true)
              })
              .catch((err: unknown) => {
                console.error('ABC synth init failed:', err)
                if (!cancelled) setNoAudio(true)
              })
          } catch (err) {
            console.error('ABC synth load failed:', err)
            if (!cancelled) setNoAudio(true)
          }
        })

        return () => cancelAnimationFrame(raf)
      } catch (e) {
        if (!cancelled) {
          setIsLoading(false)
          setError(e instanceof Error ? e.message : 'ABC 渲染失败，请检查谱子格式')
        }
      }
    }).catch(() => {
      if (!cancelled) {
        setIsLoading(false)
        setError('abcjs 加载失败，请检查网络或刷新页面')
      }
    })

    return () => {
      cancelled = true
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

      {/* 播放器容器：abcjs SynthController 将控件注入此 div */}
      <div
        id={synthIdRef.current}
        className={[
          // abcjs 的 SynthController 需要稳定的容器尺寸进行内部测量/布局
          'abcjs-synth-host relative mt-3 min-h-0',
          // 首屏不可见，避免高度跳变；但保留布局占位，方便 abcjs 正确测量
          synthReady ? 'opacity-100' : 'opacity-0',
        ].join(' ')}
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
