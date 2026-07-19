import { ExternalLink } from 'lucide-react'

function isHttpUrl(value: unknown): value is string {
  return typeof value === 'string' && /^https?:\/\//i.test(value)
}

export function ResultPanel({ value, title = '网关响应', mediaType }: { value: unknown; title?: string; mediaType?: 'audio' | 'image' | 'video' }) {
  if (value === undefined || value === null) return null
  const record = typeof value === 'object' && value ? (value as Record<string, unknown>) : null
  const mediaUrl = record && ['audioUrl', 'imageUrl', 'videoUrl', 'url'].map((key) => record[key]).find(isHttpUrl)

  return (
    <section className="overflow-hidden rounded-2xl border border-zinc-200 bg-white">
      <header className="flex items-center justify-between border-b border-zinc-100 px-4 py-3">
        <h3 className="text-sm font-semibold text-zinc-800">{title}</h3>
        {mediaUrl ? <a href={mediaUrl} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-xs font-medium text-orange-600">打开资源 <ExternalLink className="h-3.5 w-3.5" /></a> : null}
      </header>
      {mediaUrl && mediaType === 'audio' ? <div className="border-b border-zinc-100 p-4"><audio controls src={mediaUrl} className="w-full" /></div> : null}
      {mediaUrl && mediaType === 'image' ? <div className="border-b border-zinc-100 bg-zinc-50 p-4"><img src={mediaUrl} alt="网关生成结果" className="max-h-96 w-full rounded-xl object-contain" /></div> : null}
      {mediaUrl && mediaType === 'video' ? <div className="border-b border-zinc-100 bg-zinc-950 p-4"><video controls src={mediaUrl} className="max-h-96 w-full rounded-xl" /></div> : null}
      <pre className="max-h-72 overflow-auto bg-zinc-950 p-4 font-mono text-xs leading-6 text-zinc-200">{JSON.stringify(value, null, 2)}</pre>
    </section>
  )
}
