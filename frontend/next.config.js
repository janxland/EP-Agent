/** @type {import('next').NextConfig} */
const nextConfig = {
  async rewrites() {
    const backendUrl = process.env.BACKEND_URL ?? 'http://localhost:8080'
    return [
      {
        // 统一代理规则：前端 /api/* 全部转发到后端
        // api.ts 中 BASE_URL 可设为 '' （相对路径）依赖此规则代理
        // 注意：SSE stream 路径 /api/sessions/*/stream 不走此代理，
        // 直接由前端连接后端，避免 Next.js rewrites 缓冲导致流式失效。
        source: '/api/:path*',
        destination: `${backendUrl}/api/:path*`,
      },
      {
        // H5 海报静态文件代理：前端 /h5/* 转发到后端静态文件服务
        // 对应 main.py 中 app.mount("/h5", StaticFiles(...))
        source: '/h5/:path*',
        destination: `${backendUrl}/h5/:path*`,
      },
    ]
  },
  // 将后端地址暴露给前端（SSE 直连用）
  // 生产环境通过 NEXT_PUBLIC_BACKEND_URL 覆盖
  env: {
    NEXT_PUBLIC_BACKEND_URL: process.env.BACKEND_URL ?? 'http://localhost:8080',
  },
}

module.exports = nextConfig
