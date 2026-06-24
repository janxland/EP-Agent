/**
 * score entity — 乐谱领域实体
 *
 * 当前状态：Score 相关状态在 entities/session/store.ts 中管理（历史原因）
 *
 * 待迁移内容：
 *   - Score 状态从 session/store.ts 拆分到此模块
 *   - abcNotation / score / scoreHistory 等字段迁移
 *   - 提供独立的 useScoreStore hook
 *
 * 当前直接从 session/store.ts 导入：
 *   import { useScoreStore } from '@/entities/session/store'
 */
export {}
