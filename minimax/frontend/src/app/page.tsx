'use client'

import { useEffect, useState } from 'react'
import { CapabilityRail } from '@/components/console/capability-rail'
import { ResourcePanel } from '@/components/console/resource-panel'
import { InspectorPanel } from '@/components/console/inspector-panel'
import { ConsoleHeader } from '@/components/console/console-header'
import { WorkbenchTabs } from '@/components/console/workbench-tabs'
import { GatewaySettings } from '@/components/console/gateway-settings'
import { TextPlayground } from '@/features/text/text-playground'
import { SpeechPanel } from '@/features/speech/speech-panel'
import { VoiceClonePanel } from '@/features/voice/voice-clone-panel'
import { ImagePanel, MusicPanel, VideoPanel } from '@/features/media/media-panels'
import { FilesPanel } from '@/features/files/files-panel'
import { JobsPanel } from '@/features/jobs/jobs-panel'
import { getGatewayUrl } from '@/shared/api/client'
import { useConsoleStore } from '@/state/console-store'

export default function ConsolePage() {
  const capability = useConsoleStore((state) => state.capability)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [gatewayUrl, setGatewayUrl] = useState('')

  useEffect(() => setGatewayUrl(getGatewayUrl()), [])
  const shared = { gatewayUrl, onConfigure: () => setSettingsOpen(true) }

  return (
    <main className="console-shell">
      <CapabilityRail />
      <ResourcePanel />
      <section className="workbench min-w-0 bg-white">
        <ConsoleHeader gatewayUrl={gatewayUrl} onOpenSettings={() => setSettingsOpen(true)} />
        <WorkbenchTabs />
        <div className="min-h-0 flex-1 overflow-hidden">
          {capability === 'text' ? <TextPlayground {...shared} /> : null}
          {capability === 'speech' ? <SpeechPanel {...shared} /> : null}
          {capability === 'voice' ? <VoiceClonePanel {...shared} /> : null}
          {capability === 'image' ? <ImagePanel {...shared} /> : null}
          {capability === 'video' ? <VideoPanel {...shared} /> : null}
          {capability === 'music' ? <MusicPanel {...shared} /> : null}
          {capability === 'files' ? <FilesPanel {...shared} /> : null}
          {capability === 'jobs' ? <JobsPanel {...shared} /> : null}
        </div>
      </section>
      <InspectorPanel gatewayUrl={gatewayUrl} />
      <GatewaySettings open={settingsOpen} onClose={() => setSettingsOpen(false)} onSaved={setGatewayUrl} />
    </main>
  )
}
