---
name: sky-music-tools
description: "Convert Sky: Children of the Light game score JSON (txt/json file) into ABC notation, abcjs HTML sheet music, or MIDI file. Use when user provides a Sky game score file (.txt or .json) and asks to convert it to sheet music, score, ABC notation, abcjs demo, or MIDI. Also use when user says 'convert this to sheet music', 'turn this into a score', 'generate MIDI from this', 'render abcjs', or 'Sky谱转乐谱/转MIDI/转谱子'."
description-cn: "将 Sky 光·遇游戏谱 JSON 转换为 ABC 乐谱、abcjs HTML 渲染页面或标准 MIDI 文件。用户上传 .txt/.json 格式的游戏谱时触发。"
---

# Sky Music Tools

将 Sky: Children of the Light 游戏谱（JSON 格式）转换为：
- **ABC Notation** — 标准乐谱文本格式
- **abcjs HTML** — 可在浏览器渲染、播放的乐谱页面
- **MIDI 文件** — 可导入 DAW（GarageBand/Logic/FL Studio）的专业格式

---

## 工具目录结构

```
.magic/skills/sky-music-tools/
├── SKILL.md                  ← 本文件（Agent 入口）
├── models/
│   └── note_event.py         ← NoteEvent / QuantizedScore 数据结构
├── mappings/
│   └── sky_keys.py           ← Sky键位映射、MIDI/ABC转换函数
└── tools/
    ├── parser.py             ← Tool 1: 解析 JSON → QuantizedScore
    ├── abc_writer.py         ← Tool 2: QuantizedScore → ABC Notation
    ├── midi_writer.py        ← Tool 3: QuantizedScore → MIDI 文件
    └── renderer.py           ← Tool 4: ABC → abcjs HTML 页面
```

---

## 标准工作流

### 步骤 1：读取输入文件

用户给出 `.txt` 或 `.json` 文件路径，用 `read_files` 读取内容确认格式，
然后记录文件的**绝对路径**（后续 Python 脚本需要）。

Sky JSON 结构特征：
- 顶层是数组 `[{...}]`，取第一个元素
- 必含字段：`name`, `bpm`, `pitch`, `pitchLevel`, `songNotes`
- `songNotes` 每项：`{"key": "1Key5", "time": 500}`

---

### 步骤 2：解析 + 量化（parser.py）

```python
import subprocess, sys

skill_dir = "/app/.workspace/.magic/skills/sky-music-tools"
input_file = "<用户文件的绝对路径>"   # 替换为实际路径

result = subprocess.run(
    [sys.executable, f"{skill_dir}/tools/parser.py", input_file],
    capture_output=True, text=True, cwd=skill_dir
)
print(result.stdout)
print(result.stderr)
```

**关键参数说明：**

| JSON 字段 | 含义 | 处理方式 |
|-----------|------|----------|
| `bpm` | 游戏内速度（通常240） | 除以2得演奏BPM（120） |
| `pitch` | 调性（"C"/"Eb"等） | 查表得半音偏移 |
| `pitchLevel` | 额外移调半音数 | 直接叠加 |
| `songNotes[].key` | 键名"1Key1"~"1Key15" | 映射到MIDI音高 |
| `songNotes[].time` | 毫秒时间戳 | 量化到16分音符格 |

---

### 步骤 3a：生成 ABC + HTML（用户要"谱子"/"abcjs"时）

用 `run_python_snippet` 在 skill_dir 下执行完整转换：

```python
import sys, os
sys.path.insert(0, "/app/.workspace/.magic/skills/sky-music-tools")

from tools.parser    import parse_game_score
from tools.abc_writer import to_abc_notation
from tools.renderer  import render_abcjs_html

input_file  = "<用户文件绝对路径>"
output_name = "<曲名>"   # 如"斗地主"
output_dir  = f"/app/.workspace/{output_name}-谱子"
os.makedirs(output_dir, exist_ok=True)

score   = parse_game_score(input_file)
abc_str = to_abc_notation(score)
html_path = render_abcjs_html(abc_str, f"{output_dir}/index.html", title=score.title)

print(f"ABC:\n{abc_str[:300]}")
print(f"\nHTML → {html_path}")
```

输出文件：`<曲名>-谱子/index.html`

---

### 步骤 3b：生成 MIDI（用户要"MIDI"/"导入DAW"时）

先检查 mido 是否已安装，未安装则先安装：

```python
# 检查并安装 mido
import subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "mido", "-q"])
```

然后执行转换：

```python
import sys, os
sys.path.insert(0, "/app/.workspace/.magic/skills/sky-music-tools")

from tools.parser      import parse_game_score
from tools.midi_writer import to_midi

input_file  = "<用户文件绝对路径>"
output_name = "<曲名>"
output_path = f"/app/.workspace/{output_name}-谱子/{output_name}.mid"

score = parse_game_score(input_file)
result = to_midi(
    score,
    output_path,
    instrument=0,        # 0=钢琴，40=小提琴，73=长笛
    add_expression=True, # 自动力度曲线
    humanize_ticks=6     # 微时间偏移，增加人性化感
)
print(f"MIDI saved → {result}")
```

---

### 步骤 3c：同时输出 HTML + MIDI（推荐默认行为）

将步骤 3a 和 3b 合并，一次生成两个文件，给用户最完整的体验。

---

## 参数速查

### parse_game_score
| 参数 | 默认 | 说明 |
|------|------|------|
| `source` | 必填 | 文件路径或 JSON 字符串 |
| `quantize_grid` | 16 | 量化精度（8/16/32分音符） |

### to_abc_notation
| 参数 | 默认 | 说明 |
|------|------|------|
| `score` | 必填 | QuantizedScore 对象 |
| `add_repeats` | False | 实验性：自动检测重复段 |

### to_midi
| 参数 | 默认 | 说明 |
|------|------|------|
| `instrument` | 0 | GM音色编号（0=钢琴） |
| `add_expression` | True | 强拍力度增强 |
| `humanize_ticks` | 0 | 微时间偏移（4-8推荐） |

### render_abcjs_html
| 参数 | 默认 | 说明 |
|------|------|------|
| `title` | 自动提取 | 页面标题 |
| `show_source` | True | 显示ABC源码区 |

---

## GM 音色常用编号

| 编号 | 音色 | 编号 | 音色 |
|------|------|------|------|
| 0 | 钢琴 | 40 | 小提琴 |
| 8 | 钢片琴 | 46 | 竖琴 |
| 11 | 音乐盒 | 73 | 长笛 |
| 24 | 尼龙弦吉他 | 79 | 陶笛 |

---

## 常见问题处理

**Q: `mido` 未安装**
→ 执行 `pip install mido -q`，再重试

**Q: 音符偏高/偏低**
→ 检查 `pitch` 和 `pitchLevel` 字段，手动调整 `pitch_offset`

**Q: 节拍对不上**
→ 尝试调整 `quantize_grid=8`（更粗粒度）

**Q: 有多首曲子在同一 JSON**
→ `parse_game_score` 默认取 `[0]`，可在 parser.py 中改 index
