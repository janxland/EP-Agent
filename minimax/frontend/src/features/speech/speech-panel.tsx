'use client'

import { FormEvent, type ReactNode, useState } from 'react'
import { AudioLines } from 'lucide-react'
import { synthesizeSpeech } from '@/shared/api/domains/speech'
import { getErrorMessage } from '@/shared/lib/errors'
import { useConsoleStore } from '@/state/console-store'
import { Field, Input, PrimaryButton, Select, Textarea } from '@/components/ui/form-controls'
import { ErrorNotice, GatewayRequired, LoadingNotice } from '@/components/ui/status'
import { ResultPanel } from '@/components/ui/result-panel'

export function SpeechPanel({ gatewayUrl, onConfigure }: { gatewayUrl: string; onConfigure: () => void }) {
  const [model, setModel] = useState('speech-2.8-hd')
  const [text, setText] = useState('')
  const [voiceId, setVoiceId] = useState('')
  const [format, setFormat] = useState<'mp3' | 'wav' | 'pcm' | 'flac'>('mp3')
  const [speed, setSpeed] = useState(1)
  const [result, setResult] = useState<unknown>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const addRun = useConsoleStore((state) => state.addRun)
  const finishRun = useConsoleStore((state) => state.finishRun)

  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!text.trim()) return
    const id = crypto.randomUUID(); setLoading(true); setError(''); setResult(undefined)
    addRun({ id, capability: 'speech', label: `Speech · ${model}`, status: 'running', startedAt: Date.now() })
    try { const value = await synthesizeSpeech({ model, text: text.trim(), voiceId: voiceId.trim() || undefined, speed, format }); setResult(value); finishRun(id, 'succeeded') }
    catch (caught) { setError(getErrorMessage(caught)); finishRun(id, 'failed') }
    finally { setLoading(false) }
  }

  return <CapabilityForm title="Speech Synthesis" subtitle="将文本发送至网关进行真实语音合成。" gatewayUrl={gatewayUrl} onConfigure={onConfigure}>
    <form onSubmit={submit} className="space-y-5">
      <div className="grid gap-4 md:grid-cols-2"><Field label="模型"><Input value={model} onChange={(e) => setModel(e.target.value)} /></Field><Field label="Voice ID" hint="可选"><Input value={voiceId} onChange={(e) => setVoiceId(e.target.value)} placeholder="系统音色或克隆音色 ID" /></Field></div>
      <Field label="合成文本"><Textarea value={text} onChange={(e) => setText(e.target.value)} className="min-h-44" placeholder="输入需要合成的文本" /></Field>
      <div className="grid gap-4 md:grid-cols-2"><Field label="音频格式"><Select value={format} onChange={(e) => setFormat(e.target.value as typeof format)}><option value="mp3">MP3</option><option value="wav">WAV</option><option value="flac">FLAC</option><option value="pcm">PCM</option></Select></Field><Field label="语速" hint={speed.toFixed(1)}><input type="range" min="0.5" max="2" step="0.1" value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="mt-3 w-full accent-orange-500" /></Field></div>
      {error ? <ErrorNotice message={error} /> : null}{loading ? <LoadingNotice message="正在合成语音…" /> : null}
      <PrimaryButton type="submit" disabled={loading || !text.trim()}><AudioLines className="h-4 w-4" />开始合成</PrimaryButton>
    </form><ResultPanel value={result} title="语音合成响应" mediaType="audio" />
  </CapabilityForm>
}

export function CapabilityForm({ title, subtitle, gatewayUrl, onConfigure, children }: { title: string; subtitle: string; gatewayUrl: string; onConfigure: () => void; children: ReactNode }) {
  return <div className="h-full overflow-y-auto bg-zinc-50/60 p-4 sm:p-6"><div className="mx-auto max-w-4xl space-y-5"><header><p className="text-xs font-semibold uppercase tracking-[.18em] text-orange-600">Workbench</p><h2 className="mt-1 text-2xl font-bold tracking-tight text-zinc-950">{title}</h2><p className="mt-2 text-sm text-zinc-500">{subtitle}</p></header>{!gatewayUrl ? <GatewayRequired onConfigure={onConfigure} /> : null}<section className="space-y-5 rounded-3xl border border-zinc-200 bg-white p-5 shadow-panel sm:p-6">{children}</section></div></div>
}
