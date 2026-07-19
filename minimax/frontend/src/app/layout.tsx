import type { Metadata } from 'next'
import type { ReactNode } from 'react'
import { Inter, Noto_Sans_SC } from 'next/font/google'
import './globals.css'

const inter = Inter({ subsets: ['latin'], variable: '--font-inter', display: 'swap' })
const notoSansSC = Noto_Sans_SC({ subsets: ['latin'], weight: ['400', '500', '600', '700'], variable: '--font-noto-sc', display: 'swap' })

export const metadata: Metadata = {
  title: 'MiniMax 全模型控制台',
  description: '通过自有安全 API 网关使用 MiniMax 文本、语音、图像、视频与音乐能力。',
}

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="zh-CN" className={`${inter.variable} ${notoSansSC.variable}`}>
      <body>{children}</body>
    </html>
  )
}
