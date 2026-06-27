/**
 * 工作区文件系统 API
 * 对接后端 /api/workspaces/{workspace_id}/files 路由
 *
 * 三层路径规则（与 session_context.get_current_project_root() 完全一致）：
 *   有 project_id → data/workspace/{ws_id}/projects/{proj_id}/
 *   无 project_id → data/workspace/{ws_id}/  （向后兼容）
 *
 * 目录约定（相对于项目根）：
 *   .sky/          Sky 游戏谱子（JSON / ABC / MIDI）
 *   shared/        通用共享文件（图片、H5、音频等）
 *   shared/images/ 粘贴图片自动上传目标
 */

const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? ''

export interface WorkspaceFile {
  path: string    // 相对于项目根目录，如 ".sky/demo.mid"
  name: string    // 文件名
  ext: string     // 扩展名（不含点）
  size: number    // 字节数
  mime: string    // MIME 类型
  is_text: boolean
}

// ─── 文件类型常量 ─────────────────────────────────────────────────────────────

export const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'])
export const SKY_EXTS   = new Set(['json', 'abc', 'mid', 'midi'])
// Sky 谱 .txt 文件（JSON 格式）也属于谱子库，需路由到 .sky/ 目录
export const SKY_TXT_MIME_HINT = 'application/x-sky-score'

/**
 * 检测 .txt 文件是否为 Sky 谱（JSON 数组格式）
 * 通过读取文件头部内容判断，避免把普通文本误放入 .sky/
 */
export const isSkyTxtFile = async (file: File): Promise<boolean> => {
  if (!file.name.toLowerCase().endsWith('.txt')) return false
  try {
    const head = await file.slice(0, 64).text()
    return head.trimStart().startsWith('[')
  } catch {
    return false
  }
}

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
 * 构造项目文件的静态直链 URL。
 * 有 project_id 时走三层路径，否则退回工作区根。
 */
export const getFileRawUrl = (workspaceId: string, filePath: string, projectId = ''): string =>
  projectId
    ? `/workspace/${workspaceId}/projects/${projectId}/${filePath}`
    : `/workspace/${workspaceId}/${filePath}`

/** 构造项目文件的下载 URL */
export const getFileDownloadUrl = (workspaceId: string, filePath: string, projectId = ''): string => {
  const params = new URLSearchParams({ path: filePath, encoding: 'raw' })
  if (projectId) params.set('project_id', projectId)
  return `${BASE_URL}/api/workspaces/${workspaceId}/files/content?${params}`
}

// ─── 内部 fetch 封装 ──────────────────────────────────────────────────────────

const api = async (input: RequestInfo, init?: RequestInit): Promise<Response> => {
  const res = await fetch(input, init)
  if (!res.ok) throw new Error(await res.text())
  return res
}

// ─── API 函数（所有函数统一接收可选 projectId）────────────────────────────────

export const listWorkspaceFiles = async (
  workspaceId: string, projectId = '', subdir = ''
): Promise<WorkspaceFile[]> => {
  const params = new URLSearchParams()
  if (projectId) params.set('project_id', projectId)
  if (subdir)    params.set('subdir', subdir)
  const data = await api(`${BASE_URL}/api/workspaces/${workspaceId}/files?${params}`).then(r => r.json())
  return data.files as WorkspaceFile[]
}

export const readWorkspaceFile = async (
  workspaceId: string, filePath: string, projectId = ''
): Promise<string> => {
  const params = new URLSearchParams({ path: filePath, encoding: 'text' })
  if (projectId) params.set('project_id', projectId)
  const data = await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/content?${params}`).then(r => r.json())
  return data.content as string
}

export const readWorkspaceFileB64 = async (
  workspaceId: string, filePath: string, projectId = ''
): Promise<string> => {
  const params = new URLSearchParams({ path: filePath, encoding: 'base64' })
  if (projectId) params.set('project_id', projectId)
  const data = await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/content?${params}`).then(r => r.json())
  return data.content as string
}

export const writeWorkspaceFile = async (
  workspaceId: string, filePath: string, content: string,
  encoding: 'text' | 'base64' = 'text', projectId = ''
): Promise<void> => {
  const params = new URLSearchParams()
  if (projectId) params.set('project_id', projectId)
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files?${params}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: filePath, content, encoding }),
  })
}

export const deleteWorkspaceFile = async (
  workspaceId: string, filePath: string, projectId = ''
): Promise<void> => {
  const params = new URLSearchParams({ path: filePath })
  if (projectId) params.set('project_id', projectId)
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files?${params}`, { method: 'DELETE' })
}

export const copyWorkspaceFile = async (
  workspaceId: string, srcPath: string, dstPath: string, projectId = ''
): Promise<void> => {
  const params = new URLSearchParams()
  if (projectId) params.set('project_id', projectId)
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/copy?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src_path: srcPath, dst_path: dstPath }),
  })
}

export const renameWorkspaceFile = async (
  workspaceId: string, srcPath: string, newName: string, projectId = ''
): Promise<void> => {
  const params = new URLSearchParams()
  if (projectId) params.set('project_id', projectId)
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/rename?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src_path: srcPath, new_name: newName }),
  })
}

export const moveWorkspaceFile = async (
  workspaceId: string, srcPath: string, dstPath: string, projectId = ''
): Promise<void> => {
  const params = new URLSearchParams()
  if (projectId) params.set('project_id', projectId)
  await api(`${BASE_URL}/api/workspaces/${workspaceId}/files/move?${params}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ src_path: srcPath, dst_path: dstPath }),
  })
}

/**
 * 计算文件应上传到的项目内路径（自动路由目录）
 * - .sky/          : Sky 谱子（json/abc/mid/midi）+ Sky 谱 txt
 * - shared/images/ : 图片
 * - shared/        : 其他文件
 */
export const resolveUploadPath = (file: File, isSkyTxt = false): string => {
  const ext = file.name.split('.').pop()?.toLowerCase() ?? ''
  if (SKY_EXTS.has(ext) || isSkyTxt) return `.sky/${file.name}`
  if (IMAGE_EXTS.has(ext) || file.type.startsWith('image/')) return `shared/images/${file.name}`
  return `shared/${file.name}`
}

/** 上传本地文件到项目（自动路由目录，project_id 决定存储层级） */
export const uploadFileToWorkspace = async (
  workspaceId: string, file: File, destPath?: string, projectId = ''
): Promise<void> => {
  const skyTxt = !destPath && file.name.toLowerCase().endsWith('.txt')
    ? await isSkyTxtFile(file)
    : false

  const path = destPath ?? resolveUploadPath(file, skyTxt)

  const isBinary = /\.(mid|midi|mp3|wav|m4a|ogg|flac|png|jpg|jpeg|gif|webp|pdf|zip|rar|7z)$/i.test(file.name)
    || file.type.startsWith('audio/')
    || file.type.startsWith('image/')
    || file.type === 'application/octet-stream'
  const isText = !isBinary && (
    file.type.startsWith('text/') ||
    /\.(abc|txt|md|json|html|css|js|ts|xml|yaml|yml|csv|svg)$/i.test(file.name)
  )

  if (isText) {
    const content = await file.text()
    await writeWorkspaceFile(workspaceId, path, content, 'text', projectId)
  } else {
    await writeWorkspaceFile(workspaceId, path, await fileToBase64(file), 'base64', projectId)
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
