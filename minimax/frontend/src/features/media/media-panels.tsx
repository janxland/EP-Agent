'use client'

import { FormEvent, useState } from 'react'
import { ImageIcon, Music2, Video } from 'lucide-react'
import { generateImage, generateMusic, generateVideo } from '@/shared/api/domains/media'
import { getErrorMessage } from '@/shared/lib/errors'
import { useConsoleStore, type CapabilityId } from '@/state/console-store'
import { CapabilityForm } from '@/features/speech/speech-panel'
import { Field, Input, PrimaryButton, Select, Textarea } from '@/components/ui/form-controls'
import { ErrorNotice, LoadingNotice } from '@/components/ui/status'
import { ResultPanel } from '@/components/ui/result-panel'

type Kind = 'image' | 'video' | 'music'
const config = {
  image: { title: 'Image Generation', subtitle: '通过网关创建图像生成任务。', model: 'image-01', icon: ImageIcon },
  video: { title: 'Video Generation', subtitle: '通过网关创建文本或首帧驱动的视频任务。', model: 'MiniMax-Hailuo-02', icon: Video },
  music: { title: 'Music Generation', subtitle: '通过网关提交音乐生成任务并在 Jobs 中跟踪。', model: 'music-2.0', icon: Music2 },
} satisfies Record<Kind, { title: string; subtitle: string; model: string; icon: typeof ImageIcon }>

function MediaPanel({ kind, gatewayUrl, onConfigure }: { kind: Kind; gatewayUrl: string; onConfigure: () => void }) {
  const item = config[kind]
  const Icon = item.icon
  const [model, setModel] = useState(item.model)
  const [prompt, setPrompt] = useState('')
  const [secondary, setSecondary] = useState('')
  const [aspectRatio, setAspectRatio] = useState('16:9')
  const [result, setResult] = useState<unknown>()
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)
  const addRun = useConsoleStore((state) => state.addRun)
  const finishRun = useConsoleStore((state) => state.finishRun)

  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (!prompt.trim()) return
    const id = crypto.randomUUID(); setLoading(true); setError(''); setResult(undefined)
    addRun({ id, capability: kind as CapabilityId, label: `${item.title} · ${model}`, status: 'running', startedAt: Date.now() })
    try {
      const value = kind === 'image'
        ? await generateImage({ model, prompt: prompt.trim(), aspectRatio, subjectReferenceFileId: secondary.trim() || undefined })
        : kind === 'video'
          ? await generateVideo({ model, prompt: prompt.trim(), resolution: '1080P', firstFrameFileId: secondary.trim() || undefined })
          : await generateMusic({ model, prompt: prompt.trim(), lyrics: secondary.trim() || undefined })
      setResult(value); finishRun(id, 'succeeded')
    } catch (caught) { setError(getErrorMessage(caught)); finishRun(id, 'failed') }
    finally { setLoading(false) }
  }

  return <CapabilityForm title={item.title} subtitle={item.subtitle} gatewayUrl={gatewayUrl} onConfigure={onConfigure}><form onSubmit={submit} className="space-y-5"><div className="grid gap-4 md:grid-cols-2"><Field label="模型"><Input value={model} onChange={(e) => setModel(e.target.value)} /></Field>{kind === 'image' ? <Field label="画面比例"><Select value={aspectRatio} onChange={(e) => setAspectRatio(e.target.value)}><option>1:1</option><option>16:9</option><option>9:16</option><option>4:3</option><option>3:4</option></Select></Field> : <div />}</div><Field label={kind === 'music' ? '风格与创作要求' : 'Prompt'}><Textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} className="min-h-44" placeholder={kind === 'music' ? '描述曲风、情绪、结构、乐器与演唱方式' : '描述希望生成的内容'} /></Field><Field label={kind === 'image' ? '主体参考 File ID' : kind === 'video' ? '首帧 File ID' : '歌词'} hint="可选">{kind === 'music' ? <Textarea value={secondary} onChange={(e) => setSecondary(e.target.value)} /> : <Input value={secondary} onChange={(e) => setSecondary(e.target.value)} placeholder="先在 Files 上传，再填入网关返回的 File ID" />}</Field>{error ? <ErrorNotice message={error} /> : null}{loading ? <LoadingNotice message="正在创建生成任务…" /> : null}<PrimaryButton type="submit" disabled={loading || !prompt.trim()}><Icon className="h-4 w-4" />创建任务</PrimaryButton></form><ResultPanel value={result} title="生成任务响应" mediaType={kind === 'music' ? 'audio' : kind} /></CapabilityForm>
}

export const ImagePanel = (props: { gatewayUrl: string; onConfigure: () => void }) => <MediaPanel kind="image" {...props} />
export const VideoPanel = (props: { gatewayUrl: string; onConfigure: () => void }) => <MediaPanel kind="video" {...props} />
export const MusicPanel = (props: { gatewayUrl: string; onConfigure: () => void }) => <MediaPanel kind="music" {...props} />
