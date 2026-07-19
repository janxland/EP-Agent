'use client'

import { useEffect, useState } from 'react'
import { Check, Settings2, ShieldCheck, X } from 'lucide-react'
import { getGatewayUrl, saveGatewayUrl } from '@/shared/api/client'
import { Input, PrimaryButton, SecondaryButton } from '@/components/ui/form-controls'

export function GatewaySettings({ open, onClose, onSaved }: { open: boolean; onClose: () => void; onSaved: (url: string) => void }) {
  const [value, setValue] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    if (open) {
      setValue(getGatewayUrl())
      setSaved(false)
    }
  }, [open])

  if (!open) return null

  const handleSave = () => {
    saveGatewayUrl(value)
    const normalized = getGatewayUrl()
    setValue(normalized)
    setSaved(true)
    onSaved(normalized)
  }

  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4 backdrop-blur-sm" role="dialog" aria-modal="true" aria-labelledby="gateway-title">
      <div className="w-full max-w-lg rounded-3xl border border-white/60 bg-white p-6 shadow-2xl">
        <div className="flex items-start justify-between gap-4">
          <div className="flex gap-3">
            <span className="grid h-10 w-10 place-items-center rounded-xl bg-orange-50 text-orange-600"><Settings2 className="h-5 w-5" /></span>
            <div><h2 id="gateway-title" className="font-semibold text-zinc-950">API 网关设置</h2><p className="mt-1 text-sm text-zinc-500">仅保存非敏感的网关地址。</p></div>
          </div>
          <button type="button" onClick={onClose} className="rounded-lg p-2 text-zinc-400 hover:bg-zinc-100 hover:text-zinc-700" aria-label="关闭"><X className="h-5 w-5" /></button>
        </div>

        <div className="mt-6 space-y-4">
          <label className="grid gap-2 text-sm font-medium text-zinc-700">网关基础地址
            <Input value={value} onChange={(event) => setValue(event.target.value)} placeholder="https://gateway.example.com" inputMode="url" />
          </label>
          <div className="flex gap-3 rounded-2xl border border-emerald-100 bg-emerald-50 p-4 text-sm leading-6 text-emerald-800">
            <ShieldCheck className="mt-0.5 h-5 w-5 shrink-0" />
            <p>MiniMax API Key 必须只保存在你的服务端网关。本页面不提供 API Key 输入框，也不会把密钥写入源码、浏览器存储或请求参数。</p>
          </div>
          {saved ? <p className="flex items-center gap-2 text-sm text-emerald-700"><Check className="h-4 w-4" />设置已保存</p> : null}
        </div>

        <div className="mt-6 flex justify-end gap-2">
          <SecondaryButton type="button" onClick={onClose}>取消</SecondaryButton>
          <PrimaryButton type="button" onClick={handleSave}>保存设置</PrimaryButton>
        </div>
      </div>
    </div>
  )
}
