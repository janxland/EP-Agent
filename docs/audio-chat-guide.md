# 对话式音频生成使用指南

> 参考 Google MusicLM / Udio / Suno 的对话迭代模式，通过自然语言对话逐步改进生成的音乐。

## 核心交互模式

```
用户：给这首谱子配乐，中国风
AI：✅ 生成了一段中国风纯音乐（古筝+二胡，120BPM）
    建议：可以加入人声 / 试试爵士风格 / 节奏再快一点

用户：再欢快一点
AI：🔄 Prompt 调整：+ upbeat, energetic；- slow, gentle
    ✅ 重新生成（更欢快的版本）

用户：换成爵士风
AI：🔄 Prompt 调整：+ jazz, swing；- chinese traditional
    ✅ 重新生成（爵士版本）
```

## 技术架构

```
用户消息
  ↓
AudioChatRunner._route()          # Router LLM：识别意图域
  ├─ audio_generate               # 首次生成
  ├─ audio_iterate                # 迭代改进（有历史记录）
  └─ audio_cover                  # 翻唱模式

  ↓ 按意图加载工具（工具发现）
AudioChatRunner.run()             # Tool-Calling Loop
  ├─ abc_to_audio_prompt          # 提取谱子特征（首次生成）
  ├─ evolve_audio_prompt          # 进化参数（迭代改进）
  │   ├─ 规则匹配（13类关键词，无LLM消耗）
  │   └─ LLM兜底（复杂意图）
  ├─ generate_audio_minimax       # MiniMax 生成
  ├─ generate_audio_suno          # Suno 生成
  └─ generate_cover_minimax       # MiniMax 翻唱

  ↓
AudioSession 保存本轮记录
  → audio_history 追加到 Session
  → 返回结果 + diff_summary + suggestions
```

## API 端点

### POST `/api/sessions/{session_id}/audio/chat`

对话式音频生成，自动判断首次生成 or 迭代改进。

**请求体：**
```json
{
  "message": "再欢快一点",
  "provider": "auto"   // auto | minimax | suno
}
```

**响应体：**
```json
{
  "turn": 2,
  "audio_url": "https://...",
  "provider": "minimax",
  "prompt_used": "upbeat, lively, energetic, chinese traditional, guzheng",
  "style_used": "chinese traditional",
  "instrumental": true,
  "duration_ms": 25000,
  "summary": "在上次基础上增加了欢快感",
  "suggestions": ["可以加入人声", "试试爵士风格"],
  "diff_summary": "Prompt 新增：upbeat, energetic；移除：slow, gentle",
  "domain": "audio_iterate"
}
```

### GET `/api/sessions/{session_id}/audio/history`

获取所有历史轮次记录。

### DELETE `/api/sessions/{session_id}/audio/history`

清空历史，下次生成视为首次。

---

## Prompt 进化规则（evolve_audio_prompt）

| 用户说 | 新增词 | 移除词 |
|--------|--------|--------|
| 欢快/活泼/开心 | upbeat, lively, energetic | melancholic, sad, slow |
| 悲伤/忧郁 | melancholic, introspective | upbeat, lively, energetic |
| 慢/抒情 | slow, lyrical, gentle | upbeat, fast, energetic |
| 快/激烈 | fast, energetic, powerful | slow, gentle, lyrical |
| 中国风/国风 | chinese traditional, erhu, guzheng | western, electronic, jazz |
| 爵士/jazz | jazz, smooth, swing | classical, electronic, chinese |
| 古典/交响 | classical, orchestral, strings | electronic, jazz, pop |
| 电子/合成 | electronic, synthesizer, modern | acoustic, classical, traditional |
| 去掉人声/纯音乐 | — | — | instrumental=true |
| 加人声/有歌词 | — | — | instrumental=false |

> 规则匹配命中时不消耗 LLM Token；未命中时 LLM 兜底处理复杂意图。

---

## 链路闭环测试

```bash
# 1. 创建 Session
SESSION=$(curl -s -X POST http://localhost:8082/api/sessions | python3 -c "import sys,json; print(json.load(sys.stdin)['session_id'])")
echo "Session: $SESSION"

# 2. 上传谱子（使用斗地主单指）
SCORE=$(cat 斗地主【单指】.txt)
curl -s -X POST "http://localhost:8082/api/sessions/$SESSION/convert" \
  -H "Content-Type: application/json" \
  -d "{\"json_content\": $(echo $SCORE | python3 -c \"import sys,json; print(json.dumps(sys.stdin.read()))\"), \"file_name\": \"斗地主.txt\"}" \
  | python3 -m json.tool

# 3. 首次生成（中国风）
curl -s -X POST "http://localhost:8082/api/sessions/$SESSION/audio/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "给这首谱子配乐，中国风纯音乐", "provider": "auto"}' \
  | python3 -m json.tool

# 4. 迭代改进（欢快一点）
curl -s -X POST "http://localhost:8082/api/sessions/$SESSION/audio/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "再欢快一点"}' \
  | python3 -m json.tool

# 5. 再次迭代（换风格）
curl -s -X POST "http://localhost:8082/api/sessions/$SESSION/audio/chat" \
  -H "Content-Type: application/json" \
  -d '{"message": "换成爵士风格"}' \
  | python3 -m json.tool

# 6. 查看历史
curl -s "http://localhost:8082/api/sessions/$SESSION/audio/history" \
  | python3 -m json.tool

# 7. 清空历史重新开始
curl -s -X DELETE "http://localhost:8082/api/sessions/$SESSION/audio/history"
```

---

## 环境变量配置

```bash
# 必填
export LLM_API_KEY="sk-your-key"
export LLM_BASE_URL="https://api.siliconflow.cn/v1"
export LLM_MODEL="deepseek-ai/DeepSeek-V3"
export SKILL_DIR="/app/.workspace/.magic/skills/sky-music-tools"

# 音频生成（至少配置一个）
export MINIMAX_API_KEY="your_minimax_key"   # 推荐：快速、稳定
export SUNO_API_KEY="your_ttapi_key"        # 可选：有歌词时效果更好
```

---

## 前端组件说明

| 组件 | 文件 | 功能 |
|------|------|------|
| `AudioPanel` | `widgets/audio-panel/AudioPanel.tsx` | 主面板：服务商选择 + 首次生成预设 + 历史列表 + 输入框 |
| `AudioHistoryList` | `widgets/audio-panel/AudioHistoryList.tsx` | 历史轮次列表：展示 diff、播放器、建议标签 |
| `AudioChatInput` | `widgets/audio-panel/AudioChatInput.tsx` | 对话输入框：快捷建议词 + Enter 发送 |

## 与大厂实现的对比

| 平台 | 迭代方式 | 本项目 |
|------|----------|--------|
| Suno Web | 每次全新生成，无历史关联 | AudioSession 保存历史 + evolve_audio_prompt |
| Udio | Remix 功能（手动调参） | 自然语言 → 自动调参 |
| Google MusicLM | 向量空间插值 | 规则映射（13类）+ LLM 语义理解 |
| 本项目 | **规则优先 + LLM 兜底** | 确定性规则快速响应，LLM 处理复杂意图 |
