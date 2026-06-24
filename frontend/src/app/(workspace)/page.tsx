'use client'

import Link from 'next/link'

/**
 * 模式选择入口页
 * 小白模式 → /simple  （原有三栏布局，上传+编辑+导出）
 * 专业模式 → /pro     （IDE 布局，左文件树+中预览+右对话+工具卡片）
 */
export default function ModeSelectorPage() {
  return (
    <div className="min-h-screen bg-gradient-to-br from-orange-50 via-white to-amber-50 flex flex-col items-center justify-center p-8">

      {/* Logo */}
      <div className="mb-10 text-center space-y-2">
        <div className="w-16 h-16 bg-orange-500 rounded-2xl flex items-center justify-center mx-auto shadow-lg shadow-orange-200">
          <span className="text-3xl">🎵</span>
        </div>
        <h1 className="text-2xl font-bold text-gray-800">EP-Agent</h1>
        <p className="text-sm text-gray-400">Sky 谱子智能编辑 · AI 音频生成 · 音色克隆</p>
      </div>

      {/* 模式选择卡片 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 w-full max-w-2xl">

        {/* 小白模式 */}
        <Link href="/simple" className="group block">
          <div className="bg-white rounded-2xl border border-gray-100 p-6 space-y-4 shadow-sm hover:shadow-md hover:border-orange-200 transition-all group-hover:-translate-y-0.5">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-orange-50 rounded-xl flex items-center justify-center text-xl">
                🌟
              </div>
              <div>
                <h2 className="font-semibold text-gray-800">小白模式</h2>
                <p className="text-xs text-gray-400">简洁三栏布局</p>
              </div>
            </div>
            <ul className="space-y-1.5 text-xs text-gray-500">
              <li className="flex items-center gap-2"><span className="text-orange-400">✓</span> 拖拽上传 Sky JSON 谱子</li>
              <li className="flex items-center gap-2"><span className="text-orange-400">✓</span> 自然语言编辑（转调/变速/风格）</li>
              <li className="flex items-center gap-2"><span className="text-orange-400">✓</span> AI 对话式音频生成</li>
              <li className="flex items-center gap-2"><span className="text-orange-400">✓</span> 导出 ABC / MIDI / JSON</li>
            </ul>
            <div className="flex items-center justify-between pt-1">
              <span className="text-xs text-gray-300">适合初次使用</span>
              <span className="text-xs text-orange-500 font-medium group-hover:translate-x-0.5 transition-transform">
                进入 →
              </span>
            </div>
          </div>
        </Link>

        {/* 专业模式 */}
        <Link href="/pro" className="group block">
          <div className="bg-white rounded-2xl border border-gray-100 p-6 space-y-4 shadow-sm hover:shadow-md hover:border-orange-200 transition-all group-hover:-translate-y-0.5">
            <div className="flex items-center gap-3">
              <div className="w-10 h-10 bg-gray-900 rounded-xl flex items-center justify-center text-xl">
                ⚡
              </div>
              <div>
                <h2 className="font-semibold text-gray-800">专业模式</h2>
                <p className="text-xs text-gray-400">IDE 三栏布局</p>
              </div>
            </div>
            <ul className="space-y-1.5 text-xs text-gray-500">
              <li className="flex items-center gap-2"><span className="text-orange-400">✓</span> 左侧文件树 + 中央乐谱预览</li>
              <li className="flex items-center gap-2"><span className="text-orange-400">✓</span> 右侧流式对话 + 工具调用卡片</li>
              <li className="flex items-center gap-2"><span className="text-orange-400">✓</span> 实时 SSE 流式输出渲染</li>
              <li className="flex items-center gap-2"><span className="text-orange-400">✓</span> 音色克隆 · 音频生成 · 文件编辑</li>
            </ul>
            <div className="flex items-center justify-between pt-1">
              <span className="text-xs text-gray-300">参考 Cursor / Claude Code</span>
              <span className="text-xs text-orange-500 font-medium group-hover:translate-x-0.5 transition-transform">
                进入 →
              </span>
            </div>
          </div>
        </Link>
      </div>

      <p className="mt-8 text-xs text-gray-300">两种模式共享同一后端，可随时切换</p>
    </div>
  )
}
