# Next.js API Routes

此目录下的 API Route 为**遗留空目录**，当前未使用。

## 现状

前端通过 `next.config.js` 的 rewrite 规则将 `/api/*` 代理到后端 FastAPI：

```js
// next.config.js
source: '/api/:path*',
destination: `${backendUrl}/api/:path*`,
```

所有 API 调用直接走后端，无需 Next.js API Route 中间层。

## 如需使用

在对应目录下创建 `route.ts` 文件，参考 Next.js App Router 文档：
https://nextjs.org/docs/app/building-your-application/routing/route-handlers

## 目录说明

- `convert/` — 预留：Sky JSON → ABC 转换（当前由后端 `/api/sessions/:id/chat` 处理）
- `edit/`    — 预留：ABC 编辑（当前由后端 `/api/sessions/:id/chat` 处理）
- `export/`  — 预留：导出（当前由后端 `/api/sessions/:id/export` 处理）
