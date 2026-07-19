import { AlertCircle, CheckCircle2, LoaderCircle, ShieldAlert } from 'lucide-react'

export function GatewayRequired({ onConfigure }: { onConfigure: () => void }) {
  return (
    <div className="rounded-2xl border border-orange-200 bg-orange-50 p-5">
      <div className="flex gap-3">
        <ShieldAlert className="mt-0.5 h-5 w-5 shrink-0 text-orange-600" />
        <div className="space-y-2">
          <h3 className="font-semibold text-zinc-900">先连接你的安全 API 网关</h3>
          <p className="max-w-2xl text-sm leading-6 text-zinc-600">本控制台不会收集 MiniMax API Key，也不会从浏览器直连 MiniMax 官方 API。配置由你控制、在服务端保管密钥的网关后，才能执行真实生成任务。</p>
          <button type="button" onClick={onConfigure} className="text-sm font-semibold text-orange-700 underline decoration-orange-300 underline-offset-4">配置网关地址</button>
        </div>
      </div>
    </div>
  )
}

export function ErrorNotice({ message }: { message: string }) {
  return <div role="alert" className="flex gap-2 rounded-xl border border-red-200 bg-red-50 px-3.5 py-3 text-sm text-red-700"><AlertCircle className="mt-0.5 h-4 w-4 shrink-0" /><span>{message}</span></div>
}

export function SuccessNotice({ message }: { message: string }) {
  return <div className="flex gap-2 rounded-xl border border-emerald-200 bg-emerald-50 px-3.5 py-3 text-sm text-emerald-700"><CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0" /><span>{message}</span></div>
}

export function LoadingNotice({ message = '正在请求网关…' }: { message?: string }) {
  return <div className="flex items-center gap-2 text-sm text-zinc-500"><LoaderCircle className="h-4 w-4 animate-spin text-orange-500" />{message}</div>
}
