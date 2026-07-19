'use client'

import { FormEvent, useState } from 'react'
import { Check, Mic2, Play, Upload } from 'lucide-react'
import { uploadFile } from '@/shared/api/domains/files'
import { createVoiceClone } from '@/shared/api/domains/voice'
import { synthesizeSpeech } from '@/shared/api/domains/speech'
import { getErrorMessage } from '@/shared/lib/errors'
import { useConsoleStore } from '@/state/console-store'
import { CapabilityForm } from '@/features/speech/speech-panel'
import { Field, Input, PrimaryButton, Textarea } from '@/components/ui/form-controls'
import { ErrorNotice, LoadingNotice, SuccessNotice } from '@/components/ui/status'
import { ResultPanel } from '@/components/ui/result-panel'

export function VoiceClonePanel({ gatewayUrl, onConfigure }: { gatewayUrl: string; onConfigure: () => void }) {
  const [sourceFile, setSourceFile] = useState<File | null>(null)
  const [fileId, setFileId] = useState('')
  const [voiceId, setVoiceId] = useState('')
  const [name, setName] = useState('')
  const [previewText, setPreviewText] = useState('你好，这是通过安全网关生成的音色试听。')
  const [result, setResult] = useState<unknown>()
  const [error, setError] = useState('')
  const [busy, setBusy] = useState<1 | 2 | 3 | null>(null)
  const addRun = useConsoleStore((state) => state.addRun)
  const finishRun = useConsoleStore((state) => state.finishRun)

  const execute = async (step: 1 | 2 | 3, label: string, action: () => Promise<unknown>) => {
    const id = crypto.randomUUID(); setBusy(step); setError(''); setResult(undefined)
    addRun({ id, capability: 'voice', label, status: 'running', startedAt: Date.now() })
    try { const value = await action(); setResult(value); finishRun(id, 'succeeded'); return value }
    catch (caught) { setError(getErrorMessage(caught)); finishRun(id, 'failed'); return undefined }
    finally { setBusy(null) }
  }

  const handleUpload = async (event: FormEvent) => { event.preventDefault(); if (!sourceFile) return; const value = await execute(1, 'Voice · 上传样本', () => uploadFile(sourceFile, 'voice_clone')); if (value && typeof value === 'object' && 'id' in value) setFileId(String(value.id)) }
  const handleClone = async (event: FormEvent) => { event.preventDefault(); if (!fileId || !voiceId.trim()) return; await execute(2, 'Voice · 创建克隆', () => createVoiceClone({ fileId, voiceId: voiceId.trim(), name: name.trim() || undefined })) }
  const handlePreview = async (event: FormEvent) => { event.preventDefault(); if (!voiceId.trim() || !previewText.trim()) return; await execute(3, 'Voice · 试听', () => synthesizeSpeech({ model: 'speech-2.8-hd', text: previewText.trim(), voiceId: voiceId.trim(), format: 'mp3' })) }

  const stepClass = (done: boolean) => `grid h-8 w-8 shrink-0 place-items-center rounded-full text-sm font-bold ${done ? 'bg-emerald-500 text-white' : 'bg-zinc-900 text-white'}`
  return <CapabilityForm title="Voice Clone" subtitle="依次完成样本上传、创建音色与 TTS 试听；每一步都依赖真实网关响应。" gatewayUrl={gatewayUrl} onConfigure={onConfigure}>
    <div className="space-y-5">
      <form onSubmit={handleUpload} className="rounded-2xl border border-zinc-200 p-4"><div className="flex gap-3"><span className={stepClass(Boolean(fileId))}>{fileId ? <Check className="h-4 w-4" /> : '1'}</span><div className="flex-1 space-y-3"><div><h3 className="font-semibold text-zinc-900">上传声音样本</h3><p className="mt-1 text-xs text-zinc-500">网关应校验格式、时长和文件大小，并返回文件 ID。</p></div><Input type="file" accept="audio/*" onChange={(e) => setSourceFile(e.target.files?.[0] ?? null)} /><PrimaryButton type="submit" disabled={!sourceFile || busy !== null}><Upload className="h-4 w-4" />上传样本</PrimaryButton></div></div></form>
      <form onSubmit={handleClone} className="rounded-2xl border border-zinc-200 p-4"><div className="flex gap-3"><span className={stepClass(false)}>2</span><div className="flex-1 space-y-3"><div><h3 className="font-semibold text-zinc-900">创建克隆音色</h3><p className="mt-1 text-xs text-zinc-500">Voice ID 需符合你的网关和 MiniMax 账户规则。</p></div><div className="grid gap-3 md:grid-cols-2"><Field label="样本 File ID"><Input value={fileId} onChange={(e) => setFileId(e.target.value)} placeholder="步骤 1 自动填充，也可手动输入" /></Field><Field label="Voice ID"><Input value={voiceId} onChange={(e) => setVoiceId(e.target.value)} placeholder="自定义唯一音色 ID" /></Field></div><Field label="显示名称" hint="可选"><Input value={name} onChange={(e) => setName(e.target.value)} /></Field><PrimaryButton type="submit" disabled={!fileId || !voiceId.trim() || busy !== null}><Mic2 className="h-4 w-4" />创建音色</PrimaryButton></div></div></form>
      <form onSubmit={handlePreview} className="rounded-2xl border border-zinc-200 p-4"><div className="flex gap-3"><span className={stepClass(false)}>3</span><div className="flex-1 space-y-3"><div><h3 className="font-semibold text-zinc-900">生成试听</h3><p className="mt-1 text-xs text-zinc-500">使用已创建的 Voice ID 发起真实 TTS。</p></div><Field label="试听文本"><Textarea value={previewText} onChange={(e) => setPreviewText(e.target.value)} /></Field><PrimaryButton type="submit" disabled={!voiceId.trim() || !previewText.trim() || busy !== null}><Play className="h-4 w-4" />生成试听</PrimaryButton></div></div></form>
      {busy ? <LoadingNotice message={`正在执行第 ${busy} 步…`} /> : null}{error ? <ErrorNotice message={error} /> : null}{fileId ? <SuccessNotice message={`已获得样本文件 ID：${fileId}`} /> : null}<ResultPanel value={result} title="当前步骤响应" mediaType="audio" />
    </div>
  </CapabilityForm>
}
