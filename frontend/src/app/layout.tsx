import type { Metadata } from 'next'
import { Inter, Noto_Sans_SC } from 'next/font/google'
import './globals.css'
import { Providers } from './providers'

/**
 * 字体策略：
 *   - Inter      — 英文/数字/代码（拉丁字符集，覆盖 UI 标签、BPM、调号等）
 *   - Noto Sans SC — 中文（国内外均可加载，Google Fonts CDN + 本地缓存）
 *   - 两者通过 CSS variable 叠加，中文优先 Noto，英文优先 Inter
 */
const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
})

const notoSansSC = Noto_Sans_SC({
  subsets: ['latin'],   // Noto Sans SC 不支持 chinese-simplified subset，latin 即可触发中文字形加载
  weight: ['400', '500', '700'],
  variable: '--font-noto-sc',
  display: 'swap',
})

export const metadata: Metadata = {
  title: 'EP-Agent — Sky 乐谱智能编辑器',
  description: '上传 Sky JSON 谱，AI 智能转换、编辑、导出 ABC / MIDI / JSON',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" className={`${inter.variable} ${notoSansSC.variable}`}>
      {/*
        字体叠加顺序：Noto Sans SC（中文）> Inter（英文/数字）> 系统 sans-serif
        通过 tailwind.config.js fontFamily.sans 配置生效
      */}
      <body className="font-sans antialiased">
        <Providers>{children}</Providers>
      </body>
    </html>
  )
}
