'use client'

import { useEffect, useRef, useState } from 'react'

interface ABCRendererProps {
  abc: string
  title?: string
  className?: string
}

/**
 * ABCRenderer - 使用 abcjs 在浏览器端渲染 ABC 乐谱
 * 响应式，自动随 abc 字符串变化重新渲染
 */
export function ABCRenderer({ abc, title, className }: ABCRendererProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!abc || !containerRef.current) return

    // 动态加载 abcjs（避免 SSR 问题）
    import('abcjs').then((abcjs) => {
      try {
        setError(null)
        abcjs.renderAbc(containerRef.current!, abc, {
          responsive: 'resize',
          add_classes: true,
          paddingtop: 16,
          paddingbottom: 16,
          paddingright: 16,
          paddingleft: 16,
          staffwidth: containerRef.current!.clientWidth - 32,
          scale: 1.1,
        })
      } catch (e) {
        setError(e instanceof Error ? e.message : '渲染失败')
      }
    })
  }, [abc])

  if (!abc) {
    return (
      <div className={`flex items-center justify-center h-48 text-gray-400 ${className ?? ''}`}>
        <div className="text-center">
          <div className="text-4xl mb-2">🎵</div>
          <p className="text-sm">上传 JSON 谱子后，乐谱将在此显示</p>
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <div className={`flex items-center justify-center h-48 text-red-400 ${className ?? ''}`}>
        <p className="text-sm">渲染错误：{error}</p>
      </div>
    )
  }

  return (
    <div className={className}>
      {title && (
        <p className="text-xs text-gray-500 mb-1 px-4">{title}</p>
      )}
      <div ref={containerRef} className="w-full overflow-x-auto" />
    </div>
  )
}
