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

/**
 * ABCRenderer — 使用 abcjs 在浏览器端渲染 ABC 乐谱
 *
 * 防御性设计：
 *   - 渲染前校验 ABC 格式，避免 abcjs 内部 undefined.split 崩溃
 *   - 动态 import abcjs（避免 SSR 报错）
 *   - 所有异常统一 catch，降级为友好错误提示
 */
export function ABCRenderer({ abc, title, className }: ABCRendererProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // 空值或格式不完整：清空容器，不尝试渲染
    if (!abc || !containerRef.current) return
    if (!isValidABC(abc)) {
      setError('ABC 格式不完整（缺少 X: 或 K: 字段），等待 AI 返回完整谱子...')
      return
    }

    setError(null)

    // 动态加载 abcjs（避免 SSR 问题）
    import('abcjs')
      .then((abcjs) => {
        if (!containerRef.current) return
        try {
          abcjs.renderAbc(containerRef.current, abc, {
            responsive: 'resize',
            add_classes: true,
            paddingtop: 16,
            paddingbottom: 16,
            paddingright: 16,
            paddingleft: 16,
            staffwidth: Math.max(300, (containerRef.current.clientWidth || 600) - 32),
            scale: 1.1,
          })
        } catch (e) {
          // abcjs 内部解析错误（如不规范的 ABC 语法）
          setError(e instanceof Error ? e.message : 'ABC 渲染失败，请检查谱子格式')
        }
      })
      .catch(() => {
        setError('abcjs 加载失败，请检查网络或刷新页面')
      })
  }, [abc])

  // 无内容
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

  // 渲染错误
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
      <div ref={containerRef} className="w-full overflow-x-auto" />
    </div>
  )
}
