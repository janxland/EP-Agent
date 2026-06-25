/**
 * 工作区文件系统 API
 * 对接后端 /api/workspaces/{workspace_id}/files 路由
 *
 * 工作区目录约定：
 *   .sky/          Sky 游戏谱子（JSON / ABC / MIDI）
 *   shared/        通用共享文件（图片、H5、音频等）
 *   shared/images/ 粘贴图片自动上传目标
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export interface WorkspaceFile {
  path: string    // 相对于工作区根目录，如 ".sky/demo.mid"
  name: string    // 文件名
  ext: string     // 扩展名（不含点）
  size: number    // 字节数
  mime: string    // MIME 类型
  is_text: boolean
}

// ─── 文件类型常量 ─────────────────────────────────────────────────────────────

export const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'])
export const SKY_EXTS   = new Set(['json', 'abc', 'mid', 'midi'])

export const FILE_ICONS: Record<string, string> = {
  abc: '🎼', mid: '🎹', midi: '🎹', json: '🎵',
  mp3: '🔊', wav: '🔊', m4a: '🔊', ogg: '🔊', flac: '🔊',
  html: '🌐', htm: '🌐',
  png: '🖼️', jpg: '🖼️', jpeg: '🖼️', gif: '🖼️', webp: '🖼️', svg: '🖼️',
  txt: '📄', md: '📄', pdf: '📕',
}

export const getFileIcon = (ext: string): string =>
  FILE_ICONS[ext.toLowerCase()] ?? '📎'

export const fmtFileSize = (bytes: number): string =>
  bytes < 1024       ? `${bytes}B`
  : bytes < 1048576  ? `${(bytes / 1024).toFixed(1)}KB`
  :                    `${(bytes / 1048576).toFixed(1)}MB`

/**
 * 构造工作区文件的静态直链 URL（图片/二进制，可用于 <img src> 等）。
 * 走 FastAPI StaticFiles /workspace 挂载，绕开 API 路由，浏览器可直接缓存。
 */
export const getFileRawUrl = (workspaceId: string, filePath: string): string =>
  `/workspace/${workspaceId}/${filePath}`

/** 构造工作区文件的下载 URL（raw 二进制流，浏览器直接下载，不经过 base64 包装） */
export const getFileDownloadUrl = (workspaceId: string, filePath: string): string =>
  `${BASE_URL}/api/workspaces/${workspaceId}/files/content?path=${encodeURIComponent(filePath)}&encoding=raw`

// ─── API 函数 ─────────────────────────────────────────────────────────────────

const api = async (input: RequestInfo, init?: RequestInit): Promise<Response> => {
  const res = await fetch(input, init)
  if (!res.ok) throw new Error(await res.text())
  return res
}

export const listWorkspaceFiles = async (workspaceId: string, subdir = ''): Promise<WorkspaceFile[]> => {
  const params = new URLSearchParams(subdir ? { subdir } : {})
  const data = await api(`${BASE_URL}/api/workspaces/${workspaceId}/files?${params}`).then(r => r.json())
  return data.files as WorkspaceFile[]
}

export const readWorkspaceFile = async (workspaceId: string, filePath: string): Promise<string> => {
  const params = new URLSearchParams({ path: filePath, encoding: 'text' })
  const data = await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/content?${params}`).then(r => r.json())
  return data.content as string
}

export const readWorkspaceFileB64 = async (workspaceId: string, filePath: string): Promise<string> => {
  const params = new URLSearchParams({ path: filePath, encoding: 'base64' })
  const data = await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/content?${params}`).then(r => r.json())
  return data.content as string
}

export const writeWorkspaceFile = async (
  workspaceId: string, filePath: string, content: string, encoding: 'text' | 'base64' = 'text'
): Promise<void> => {
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: filePath, content, encoding }),
  })
}

export const deleteWorkspaceFile = async (workspaceId: string, filePath: string): Promise<void> => {
  const params = new URLSearchParams({ path: filePath })
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files?${params}`, { method: 'DELETE' })
}

export const copyWorkspaceFile = async (workspaceId: string, srcPath: string, dstPath: string): Promise<void> => {
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/copy`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src_path: srcPath, dst_path: dstPath }),
  })
}

export const renameWorkspaceFile = async (workspaceId: string, srcPath: string, newName: string): Promise<void> => {
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/rename`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src_path: srcPath, new_name: newName }),
  })
}

export const moveWorkspaceFile = async (workspaceId: string, srcPath: string, dstPath: string): Promise<void> => {
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/move`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src_path: srcPath, dst_path: dstPath }),
  })
}

/** 上传本地文件到工作区（自动路由目录） */
export const uploadFileToWorkspace = async (workspaceId: string, file: File, destPath?: string): Promise<void> => {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  const path = destPath
    ?? (SKY_EXTS.has(ext)   ? `.sky/${file.name}`
    :  IMAGE_EXTS.has(ext) || file.type.startsWith('image/') ? `shared/images/${file.name}`
    :  `shared/${file.name}`)

  // 严格白名单判断文本：音频/MIDI/图片等二进制文件即使扩展名在列表里也走 base64
  const isBinary = /\.(mid|midi|mp3|wav|m4a|ogg|flac|png|jpg|jpeg|gif|webp|pdf|zip|rar|7z)$/i.test(file.name)
    || file.type.startsWith('audio/')
    || file.type.startsWith('image/')
    || file.type === 'application/octet-stream'
  const isText = !isBinary && (
    file.type.startsWith('text/') ||
    /\.(abc|txt|md|json|html|css|js|ts|xml|yaml|yml|csv|svg)$/i.test(file.name)
  )

  if (isText) {
    await writeWorkspaceFile(workspaceId, path, await file.text(), 'text')
  } else {
    await writeWorkspaceFile(workspaceId, path, await fileToBase64(file), 'base64')
  }
}

export const fileToBase64 = (file: File): Promise<string> =>
  new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => {
      const result = reader.result as string
      resolve(result.includes(',') ? result.split(',')[1] : result)
    }
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })

// ─── 保留兼容性导出（旧代码引用）────────────────────────────────────────────

export const isSkyFile = (path: string): boolean => path.startsWith('.sky/')

export const getDirLabel = (path: string): string =>
  path.startsWith('.sky/') ? '谱子库'
  : path.startsWith('shared/') ? '共享文件'
  : path.split('/').slice(0, -1).join('/') || '根目录'
