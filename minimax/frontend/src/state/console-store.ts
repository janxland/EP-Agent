'use client'

import { create } from 'zustand'

export type CapabilityId = 'text' | 'image' | 'video' | 'speech' | 'voice' | 'music' | 'files' | 'jobs'

export type WorkbenchTab = {
  id: string
  capability: CapabilityId
  title: string
}

export type RunState = {
  id: string
  capability: CapabilityId
  label: string
  status: 'running' | 'succeeded' | 'failed' | 'cancelled'
  startedAt: number
}

export type ChatMessage = {
  id: string
  role: 'user' | 'assistant'
  content: string
}

type ConsoleState = {
  capability: CapabilityId
  tabs: WorkbenchTab[]
  activeTabId: string
  runs: RunState[]
  chatMessages: ChatMessage[]
  streamText: string
  resourceOpen: boolean
  inspectorOpen: boolean
  setCapability: (capability: CapabilityId) => void
  closeTab: (id: string) => void
  setActiveTab: (id: string) => void
  addRun: (run: RunState) => void
  finishRun: (id: string, status: RunState['status']) => void
  addChatMessage: (message: ChatMessage) => void
  setStreamText: (text: string) => void
  appendStreamText: (text: string) => void
  clearChat: () => void
  toggleResource: () => void
  toggleInspector: () => void
}

const titles: Record<CapabilityId, string> = {
  text: 'Text Playground',
  image: 'Image Generation',
  video: 'Video Generation',
  speech: 'Speech Synthesis',
  voice: 'Voice Clone',
  music: 'Music Generation',
  files: 'Files',
  jobs: 'Jobs',
}

const initialTab: WorkbenchTab = { id: 'text', capability: 'text', title: titles.text }

export const useConsoleStore = create<ConsoleState>((set) => ({
  capability: 'text',
  tabs: [initialTab],
  activeTabId: initialTab.id,
  runs: [],
  chatMessages: [],
  streamText: '',
  resourceOpen: false,
  inspectorOpen: false,
  setCapability: (capability) =>
    set((state) => {
      const existing = state.tabs.find((tab) => tab.capability === capability)
      const tab = existing ?? { id: capability, capability, title: titles[capability] }
      return {
        capability,
        tabs: existing ? state.tabs : [...state.tabs, tab],
        activeTabId: tab.id,
        resourceOpen: false,
      }
    }),
  closeTab: (id) =>
    set((state) => {
      if (state.tabs.length === 1) return state
      const index = state.tabs.findIndex((tab) => tab.id === id)
      const tabs = state.tabs.filter((tab) => tab.id !== id)
      const fallback = tabs[Math.max(0, index - 1)] ?? tabs[0]
      const active = state.activeTabId === id ? fallback : tabs.find((tab) => tab.id === state.activeTabId) ?? fallback
      return { tabs, activeTabId: active.id, capability: active.capability }
    }),
  setActiveTab: (id) =>
    set((state) => {
      const tab = state.tabs.find((item) => item.id === id)
      return tab ? { activeTabId: id, capability: tab.capability } : state
    }),
  addRun: (run) => set((state) => ({ runs: [run, ...state.runs].slice(0, 20) })),
  finishRun: (id, status) => set((state) => ({ runs: state.runs.map((run) => (run.id === id ? { ...run, status } : run)) })),
  addChatMessage: (message) => set((state) => ({ chatMessages: [...state.chatMessages, message] })),
  setStreamText: (streamText) => set({ streamText }),
  appendStreamText: (text) => set((state) => ({ streamText: state.streamText + text })),
  clearChat: () => set({ chatMessages: [], streamText: '' }),
  toggleResource: () => set((state) => ({ resourceOpen: !state.resourceOpen })),
  toggleInspector: () => set((state) => ({ inspectorOpen: !state.inspectorOpen })),
}))
