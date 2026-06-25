# H5 模板开发指南

## 新增模板只需 3 步，零代码修改

```
h5-templates/
└── your-theme/          # 1. 创建文件夹（文件夹名即模板名）
    ├── index.html       # 2. 放入模板 HTML（用 {{VAR}} 占位符）
    ├── meta.json        # 3. 放入元数据（Agent 自动发现）
    └── assets/          # 可选：图片等静态资源
```

重启服务后（或调用 `reload_template_registry()`）即可生效。  
**无需修改任何 Python 代码、提示词或工具签名。**

---

## meta.json 格式

```json
{
  "name":        "your-theme",
  "label":       "模板显示名",
  "desc":        "模板风格描述（Agent 用于选模板时参考）",
  "mood":        "情绪关键词，如 dark premium / cyber electric",
  "intent_keys": ["触发词1", "触发词2", "..."],
  "extra_vars":  {
    "LYRIC_LINE": "歌词一行（可选，说明给 Agent 看）",
    "YOUR_VAR":   "变量用途说明"
  },
  "theme_preset": {
    "ACCENT_COLOR":   "#FF375F",
    "BG_COLOR":       "#0A0A0F",
    "CARD_BG":        "rgba(28,28,30,0.85)",
    "TEXT_COLOR":     "#FFFFFF",
    "TEXT_SUB":       "rgba(255,255,255,0.55)",
    "GRADIENT":       "linear-gradient(135deg, #1a0010 0%, #0A0A0F 50%, #001020 100%)",
    "ABC_SVG_FILTER": "invert(1) brightness(0.9)"
  }
}
```

### 字段说明

| 字段 | 必填 | 说明 |
|------|------|------|
| `name` | 否 | 默认取文件夹名 |
| `label` | 否 | Agent 展示给用户的名称 |
| `desc` | 否 | 风格描述，Agent 选模板时参考 |
| `mood` | 否 | 情绪标签 |
| `intent_keys` | 否 | 用户说这些词时优先选此模板 |
| `extra_vars` | 否 | 模板专属变量声明（key=占位符名，value=说明） |
| `theme_preset` | 否 | 颜色/样式预设，缺省时用兜底值 |

---

## index.html 占位符

### 通用变量（所有模板可用）

| 占位符 | 说明 |
|--------|------|
| `{{TITLE}}` | 乐曲标题 |
| `{{COMPOSER}}` | 作曲者 |
| `{{BPM}}` | 节拍速度 |
| `{{KEY}}` | 调号 |
| `{{FORMAT_LABEL}}` | 格式标签（MIDI / ABC Notation） |
| `{{MIDI_URL}}` | MIDI 相对路径（浏览器端 CDN 库加载） |
| `{{ABC_CONTENT}}` | ABC 内容（JS 转义后） |
| `{{ABC_SECTION}}` | ABC 渲染区块 HTML |
| `{{ACCENT_COLOR}}` | 强调色 |
| `{{BG_COLOR}}` | 背景色 |
| `{{CARD_BG}}` | 卡片背景 |
| `{{TEXT_COLOR}}` | 主文字色 |
| `{{TEXT_SUB}}` | 副文字色 |
| `{{GRADIENT}}` | 渐变背景 |
| `{{ABC_SVG_FILTER}}` | ABC SVG 滤镜 |
| `{{VIDEO_URL}}` | 视频链接 |
| `{{VIDEO_SECTION}}` | 视频区块 HTML |
| `{{EXTRA_INFO}}` | 额外说明文字 |
| `{{EXTRA_HTML}}` | 额外说明区块 HTML |

### 模板专属变量

在 `meta.json` 的 `extra_vars` 中声明，Agent 调用时通过 `extra_vars` 参数传入：

```json
// Agent 调用示例
generate_h5_from_abc(
  abc="...",
  template="miku",
  extra_vars="{\"LYRIC_LINE\": \"世界上最遥远的距离\"}"
)
```

---

## 现有模板一览

| 文件夹 | 风格 | 专属变量 |
|--------|------|----------|
| `apple/` | 苹果深色毛玻璃，旋转唱片 | 无 |
| `miku/` | 初音赛博全息 | `LYRIC_LINE` |
| `luoxiaohei/` | 罗小黑深夜星空 | `CAT_EMOJI`, `NIGHT_MOOD` |
| `neon/` | 霓虹几何 Synthwave | 无 |
| `ins/` | INS 玫瑰金美学 | `LYRIC_LINE` |

---

## 复制模板快速开始

```bash
# 复制现有模板作为起点
cp -r h5-templates/apple h5-templates/my-new-theme

# 修改 meta.json 的 name/label/intent_keys/theme_preset
# 修改 index.html 的样式和布局
# 重启服务或调用 reload_template_registry()
```
