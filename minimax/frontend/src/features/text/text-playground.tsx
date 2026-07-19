'use client'

import { FormEvent, useRef, useState } from 'react'
import { Eraser, Send, Square } from 'lucide-react'
import { streamTextChat, type TextMessage } from '@/shared/api/domains/text'
import { getErrorMessage } from '@/shared/lib/errors'
import { useConsoleStore } from '@/state/console-store'
import { Field, Input, PrimaryButton, SecondaryButton, Textarea } from '@/components/ui/form-controls'
import { ErrorNotice, GatewayRequired } from '@/components/ui/status'

export function TextPlayground({ gatewayUrl, onConfigure }: { gatewayUrl: string; onConfigure: () => void }) {
  const [model, setModel] = useState('MiniMax-M2.5')
  const [systemPrompt, setSystemPrompt] = useState('')
  const [input, setInput] = useState('')
  const [temperature, setTemperature] = useState(0.7)
  const [error, setError] = useState('')
  const [streaming, setStreaming] = useState(false)
  const controllerRef = useRef<AbortController | null>(null)
  const messages = useConsoleStore((state) => state.chatMessages)
  const streamText = useConsoleStore((state) => state.streamText)
  const addMessage = useConsoleStore((state) => state.addChatMessage)
  const setStreamText = useConsoleStore((state) => state.setStreamText)
  const appendStreamText = useConsoleStore((state) => state.appendStreamText)
  const clearChat = useConsoleStore((state) => state.clearChat)
  const addRun = useConsoleStore((state) => state.addRun)
  const finishRun = useConsoleStore((state) => state.finishRun)

  const submit = async (event: FormEvent) => {
    event.preventDefault()
    const prompt = input.trim()
    if (!prompt || streaming) return
    setError('')
    setInput('')
    setStreamText('')
    addMessage({ id: crypto.randomUUID(), role: 'user', content: prompt })
    const runId = crypto.randomUUID()
    addRun({ id: runId, capability: 'text', label: `Text · ${model}`, status: 'running', startedAt: Date.now() })
    const controller = new AbortController()
    controllerRef.current = controller
    setStreaming(true)

    const history: TextMessage[] = [
      ...(systemPrompt.trim() ? [{ role: 'system' as const, content: systemPrompt.trim() }] : []),
      ...messages.map((message) => ({ role: message.role, content: message.content })),
      { role: 'user', content: prompt },
    ]
    let completed = ''
    try {
      await streamTextChat({ model, messages: history, temperature }, {
        signal: controller.signal,
        onDelta: (delta) => {
          completed += delta
          appendStreamText(delta)
        },
      })
      if (completed) addMessage({ id: crypto.randomUUID(), role: 'assistant', content: completed })
      setStreamText('')
      finishRun(runId, 'succeeded')
    } catch (caught) {
      const aborted = caught instanceof DOMException && caught.name === 'AbortError'
      if (completed) addMessage({ id: crypto.randomUUID(), role: 'assistant', content: completed })
      setStreamText('')
      setError(getErrorMessage(caught))
      finishRun(runId, aborted ? 'cancelled' : 'failed')
    } finally {
      controllerRef.current = null
      setStreaming(false)
    }
  }

  return (
    <div className="grid h-full min-h-0 grid-cols-1 xl:grid-cols-[minmax(0,1fr)_280px]">
      <section className="flex min-h-0 flex-col bg-white">
        <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-4 sm:p-6">
          {!gatewayUrl ? <GatewayRequired onConfigure={onConfigure} /> : null}
          {messages.length === 0 && !streamText ? <div className="grid min-h-64 place-items-center rounded-3xl border border-dashed border-zinc-200 bg-zinc-50/60 p-8 text-center"><div><p className="font-semibold text-zinc-800">开始一次真实的流式对话</p><p className="mt-2 text-sm text-zinc-500">消息只会发送到你配置的安全网关。控制台不会伪造回复。</p></div></div> : null}
          {messages.map((message) => <article key={message.id} className={`max-w-3xl rounded-2xl px-4 py-3 text-sm leading-7 ${message.role === 'user' ? 'ml-auto bg-zinc-900 text-white' : 'border border-zinc-200 bg-white text-zinc-700 shadow-sm'}`}><p className="mb-1 text-[10px] font-semibold uppercase tracking-wider opacity-50">{message.role}</p><div className="whitespace-pre-wrap">{message.content}</div></article>)}
          {streamText ? <article className="max-w-3xl rounded-2xl border border-orange-200 bg-orange-50/40 px-4 py-3 text-sm leading-7 text-zinc-700"><p className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-orange-500">assistant · streaming</p><div className="whitespace-pre-wrap">{streamText}<span className="ml-1 inline-block h-4 w-1 animate-pulse bg-orange-500 align-middle" /></div></article> : null}
          {error ? <ErrorNotice message={error} /> : null}
        </div>
        <form onSubmit={submit} className="border-t border-zinc-200 bg-white p-3 sm:p-4">
          <div className="rounded-2xl border border-zinc-200 bg-white p-2 shadow-panel focus-within:border-orange-300 focus-within:ring-4 focus-within:ring-orange-50">
            <Textarea value={input} onChange={(event) => setInput(event.target.value)} placeholder="输入消息；Enter 由浏览器默认换行，点击发送执行…" className="min-h-20 border-0 px-2 py-2 focus:ring-0" disabled={streaming} />
            <div className="flex items-center justify-between gap-2 border-t border-zinc-100 pt-2">
              <SecondaryButton type="button" onClick={clearChat} disabled={streaming}><Eraser className="h-4 w-4" />清空</SecondaryButton>
              {streaming ? <PrimaryButton type="button" onClick={() => controllerRef.current?.abort()}><Square className="h-3.5 w-3.5 fill-current" />停止</PrimaryButton> : <PrimaryButton type="submit" disabled={!input.trim()}><Send className="h-4 w-4" />发送</PrimaryButton>}
            </div>
          </div>
        </form>
      </section>
      <aside className="border-l border-zinc-200 bg-zinc-50/70 p-4">
        <h3 className="mb-4 text-xs font-semibold uppercase tracking-[.15em] text-zinc-400">Parameters</h3>
        <div className="space-y-4">
          <Field label="模型"><Input value={model} onChange={(event) => setModel(event.target.value)} /></Field>
          <Field label="Temperature" hint={temperature.toFixed(1)}><input type="range" min="0" max="2" step="0.1" value={temperature} onChange={(event) => setTemperature(Number(event.target.value))} className="w-full accent-orange-500" /></Field>
          <Field label="System prompt"><Textarea value={systemPrompt} onChange={(event) => setSystemPrompt(event.target.value)} className="min-h-32" placeholder="可选：定义角色与输出约束" /></Field>
        </div>
      </aside>
    </div>
  )
}
