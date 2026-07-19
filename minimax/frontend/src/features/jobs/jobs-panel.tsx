'use client'

import { useState } from 'react'
import { Ban, RefreshCw } from 'lucide-react'
import { cancelJob, listJobs } from '@/shared/api/domains/jobs'
import type { JobRecord, JobStatus } from '@/shared/types/api'
import { getErrorMessage } from '@/shared/lib/errors'
import { CapabilityForm } from '@/features/speech/speech-panel'
import { SecondaryButton, Select } from '@/components/ui/form-controls'
import { ErrorNotice, LoadingNotice } from '@/components/ui/status'

const badge: Record<JobStatus, string> = { queued: 'bg-zinc-100 text-zinc-600', running: 'bg-orange-50 text-orange-700', succeeded: 'bg-emerald-50 text-emerald-700', failed: 'bg-red-50 text-red-700', cancelled: 'bg-zinc-100 text-zinc-500' }

export function JobsPanel({ gatewayUrl, onConfigure }: { gatewayUrl: string; onConfigure: () => void }) {
  const [items, setItems] = useState<JobRecord[]>([])
  const [status, setStatus] = useState<JobStatus | ''>('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const refresh = async () => { setLoading(true); setError(''); try { const value = await listJobs({ status: status || undefined }); setItems(value.items) } catch (caught) { setError(getErrorMessage(caught)) } finally { setLoading(false) } }
  const cancel = async (id: string) => { setLoading(true); setError(''); try { const value = await cancelJob(id); setItems((current) => current.map((item) => item.id === id ? value : item)) } catch (caught) { setError(getErrorMessage(caught)) } finally { setLoading(false) } }

  return <CapabilityForm title="Jobs" subtitle="查询图像、视频、音乐等异步任务的真实状态和输出。" gatewayUrl={gatewayUrl} onConfigure={onConfigure}><div className="flex flex-wrap items-end gap-3"><label className="grid min-w-48 gap-1.5 text-sm font-medium text-zinc-700">状态筛选<Select value={status} onChange={(e) => setStatus(e.target.value as JobStatus | '')}><option value="">全部</option><option value="queued">Queued</option><option value="running">Running</option><option value="succeeded">Succeeded</option><option value="failed">Failed</option><option value="cancelled">Cancelled</option></Select></label><SecondaryButton type="button" onClick={refresh} disabled={loading}><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新任务</SecondaryButton></div>{error ? <ErrorNotice message={error} /> : null}{loading ? <LoadingNotice message="正在查询任务…" /> : null}<div className="space-y-3">{items.length === 0 ? <div className="rounded-2xl border border-dashed border-zinc-200 p-10 text-center text-sm text-zinc-400">尚未从网关读取到任务</div> : items.map((job) => <article key={job.id} className="rounded-2xl border border-zinc-200 p-4"><div className="flex flex-wrap items-start justify-between gap-3"><div><div className="flex items-center gap-2"><h3 className="font-semibold text-zinc-900">{job.capability}</h3><span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase ${badge[job.status]}`}>{job.status}</span></div><p className="mt-1 font-mono text-[11px] text-zinc-400">{job.id}</p></div>{job.status === 'queued' || job.status === 'running' ? <button type="button" onClick={() => cancel(job.id)} className="inline-flex items-center gap-1.5 rounded-lg border border-red-200 px-2.5 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50"><Ban className="h-3.5 w-3.5" />取消</button> : null}</div>{typeof job.progress === 'number' ? <div className="mt-4 h-1.5 overflow-hidden rounded-full bg-zinc-100"><div className="h-full bg-orange-500 transition-all" style={{ width: `${Math.max(0, Math.min(100, job.progress))}%` }} /></div> : null}{job.error?.message ? <p className="mt-3 text-xs text-red-600">{job.error.message}</p> : null}</article>)}</div></CapabilityForm>
}
