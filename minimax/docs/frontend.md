# MiniMax Frontend 实现说明

## 范围

本前端位于 `minimax/frontend`，是独立 Next.js App Router 项目。没有创建后端服务，也没有修改工作区根级 `frontend` 或任何 backend 文件。

## 设计

- 四区桌面布局：能力栏、资源栏、中央 Workbench、Inspector。
- 小屏保留能力栏；资源栏与 Inspector 转为可折叠抽屉。
- 视觉使用 Noto Sans SC / Inter、白灰背景、橙色主色、圆角卡片、轻边框与专业 IDE tabs。
- 主导航统一使用 `lucide-react` 图标，没有使用 emoji 作为能力图标。

## 功能

- Text：SSE 流式聊天、停止、上下文与 System Prompt。
- Speech：TTS 参数表单与真实网关响应。
- Voice Clone：样本上传、克隆、试听三步流程。
- Image / Video / Music：创建真实异步生成任务。
- Files：上传、列表、删除网关文件。
- Jobs：筛选、刷新与取消任务。
- 网关未配置时显示清晰说明，所有操作失败均显示可读错误，不生成 mock 成功数据。

## 前端边界

- `src/shared/api/client.ts`：统一 JSON、FormData、SSE 请求与错误处理。
- `src/shared/sse/parser.ts`：处理多行 data、注释心跳、`[DONE]`、CRLF 和残余 buffer。
- `src/shared/api/domains/*`：按 Text、Speech、Voice、Media、Files、Jobs 拆分。
- Zustand 仅保存当前能力、tabs、会话运行记录与文本流；网关地址为非敏感 localStorage 偏好。
- MiniMax API Key 不进入前端源码、环境变量、localStorage 或请求 body。

## 外部依赖

必须由部署方实现 `gateway-contract.md` 中的安全网关接口。未实现网关时，UI 可启动、浏览和配置，但无法获得生成结果。
