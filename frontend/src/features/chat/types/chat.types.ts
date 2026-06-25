// 对话消息类型 - 对齐 OpenAI message 结构

export type ChatRunStatus = 'idle' | 'running' | 'completed' | 'failed'

export interface ToolCall {
  id: string
  type: 'function'
  function: { name: string; arguments: string }
}

export interface ChatUserMessage {
  id: string
  role: 'user'
  content: string
  createdAt: string
}

export interface ChatAssistantMessage {
  id: string
  role: 'assistant'
  content: string
  reasoning_content?: string
  tool_calls?: ToolCall[]
  createdAt: string
  kind?: 'turn' | 'final'
}

export interface ChatToolMessage {
  id: string
  role: 'tool'
  tool_call_id: string
  name?: string
  content: string
  createdAt: string
}

export type ChatMessage = ChatUserMessage | ChatAssistantMessage | ChatToolMessage
