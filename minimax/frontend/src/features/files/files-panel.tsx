'use client'

import { FormEvent, useState } from 'react'
import { FileUp, RefreshCw, Trash2 } from 'lucide-react'
import { listFiles, removeFile, uploadFile } from '@/shared/api/domains/files'
import type { FileRecord } from '@/shared/types/api'
import { getErrorMessage } from '@/shared/lib/errors'
import { CapabilityForm } from '@/features/speech/speech-panel'
import { Field, Input, PrimaryButton, SecondaryButton } from '@/components/ui/form-controls'
import { ErrorNotice, LoadingNotice } from '@/components/ui/status'

export function FilesPanel({ gatewayUrl, onConfigure }: { gatewayUrl: string; onConfigure: () => void }) {
  const [items, setItems] = useState<FileRecord[]>([])
  const [file, setFile] = useState<File | null>(null)
  const [purpose, setPurpose] = useState('general')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const refresh = async () => { setLoading(true); setError(''); try { const value = await listFiles(); setItems(value.items) } catch (caught) { setError(getErrorMessage(caught)) } finally { setLoading(false) } }
  const submit = async (event: FormEvent) => { event.preventDefault(); if (!file) return; setLoading(true); setError(''); try { await uploadFile(file, purpose.trim() || 'general'); setFile(null); const value = await listFiles(); setItems(value.items) } catch (caught) { setError(getErrorMessage(caught)) } finally { setLoading(false) } }
  const remove = async (id: string) => { setLoading(true); setError(''); try { await removeFile(id); setItems((current) => current.filter((item) => item.id !== id)) } catch (caught) { setError(getErrorMessage(caught)) } finally { setLoading(false) } }

  return <CapabilityForm title="Files" subtitle="管理由你的网关代理上传并托管的真实文件资产。" gatewayUrl={gatewayUrl} onConfigure={onConfigure}><form onSubmit={submit} className="grid gap-4 rounded-2xl border border-zinc-200 bg-zinc-50 p-4 md:grid-cols-[1fr_180px_auto]"><Field label="选择文件"><Input type="file" onChange={(e) => setFile(e.target.files?.[0] ?? null)} /></Field><Field label="Purpose"><Input value={purpose} onChange={(e) => setPurpose(e.target.value)} /></Field><PrimaryButton type="submit" className="self-end" disabled={!file || loading}><FileUp className="h-4 w-4" />上传</PrimaryButton></form><div className="flex items-center justify-between"><h3 className="font-semibold text-zinc-900">文件列表</h3><SecondaryButton type="button" onClick={refresh} disabled={loading}><RefreshCw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />刷新</SecondaryButton></div>{error ? <ErrorNotice message={error} /> : null}{loading ? <LoadingNotice /> : null}<div className="overflow-hidden rounded-2xl border border-zinc-200"><div className="grid grid-cols-[minmax(0,1fr)_120px_90px] bg-zinc-50 px-4 py-2 text-xs font-semibold text-zinc-500"><span>文件</span><span>Purpose</span><span className="text-right">操作</span></div>{items.length === 0 ? <p className="p-8 text-center text-sm text-zinc-400">尚未从网关读取到文件</p> : items.map((item) => <div key={item.id} className="grid grid-cols-[minmax(0,1fr)_120px_90px] items-center border-t border-zinc-100 px-4 py-3 text-sm"><div className="min-w-0"><p className="truncate font-medium text-zinc-800">{item.filename}</p><p className="truncate font-mono text-[10px] text-zinc-400">{item.id}</p></div><span className="truncate text-xs text-zinc-500">{item.purpose ?? '—'}</span><div className="text-right"><button type="button" onClick={() => remove(item.id)} className="rounded-lg p-2 text-zinc-400 hover:bg-red-50 hover:text-red-600" aria-label={`删除 ${item.filename}`}><Trash2 className="h-4 w-4" /></button></div></div>)}</div></CapabilityForm>
}
