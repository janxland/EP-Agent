import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatBPM(bpm: number): string {
  return `♩= ${Math.round(bpm)}`
}

export function formatTimeSig(num: number, den: number): string {
  return `${num}/${den}`
}

export function formatKey(key: string): string {
  const keyNames: Record<string, string> = {
    C: 'C 大调', Am: 'A 小调',
    G: 'G 大调', Em: 'E 小调',
    D: 'D 大调', Bm: 'B 小调',
    A: 'A 大调', 'F#m': 'F# 小调',
    E: 'E 大调', 'C#m': 'C# 小调',
    B: 'B 大调', 'G#m': 'G# 小调',
    F: 'F 大调', Dm: 'D 小调',
    Bb: 'Bb 大调', Gm: 'G 小调',
    Eb: 'Eb 大调', Cm: 'C 小调',
    Ab: 'Ab 大调', Fm: 'F 小调',
    Db: 'Db 大调', Bbm: 'Bb 小调',
    Gb: 'Gb 大调', Ebm: 'Eb 小调',
  }
  return keyNames[key] ?? key
}

export function downloadBlob(data: Blob, filename: string) {
  const url = URL.createObjectURL(data)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
