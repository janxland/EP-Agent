"""
GPT-SoVITS 接口诊断脚本
在宿主机运行：python EP-Agent/backend/scripts/diag_sovits.py
"""
import httpx
import sys
from pathlib import Path

BASE_URL = "http://localhost:9880"

# ── 1. 健康检查 ────────────────────────────────────────────────
print("=== 1. 健康检查 ===")
try:
    r = httpx.get(f"{BASE_URL}/docs", timeout=5)
    print(f"GET /docs → {r.status_code}")
    if r.status_code == 200:
        print(r.text[:300])
except Exception as e:
    print(f"连接失败: {e}")
    sys.exit(1)

# ── 2. 找一个测试音频 ──────────────────────────────────────────
test_audio_candidates = [
    Path("EP-Agent/sovits-installer/data/GPT-SoVITS/test_assets/ww_encore_greeting_en.ogg"),
    Path("furina/vo_furina_dialog_idle.wav"),
]
ref_audio = None
for p in test_audio_candidates:
    if p.exists():
        ref_audio = p
        break

if not ref_audio:
    print("❌ 找不到测试音频，请手动指定路径")
    sys.exit(1)

print(f"\n使用参考音频: {ref_audio} ({ref_audio.stat().st_size} bytes)")

# ── 3. 测试路径模式（JSON）────────────────────────────────────
print("\n=== 3. 测试路径模式（JSON + ref_audio_path）===")
# 注意：路径模式需要传容器内路径
# 如果音频在 sovits-installer/data/ 下，容器路径是 /workspace/...
container_path = str(ref_audio).replace(
    "EP-Agent/sovits-installer/data",
    ""
).replace("\\", "/")
if not container_path.startswith("/workspace"):
    container_path = "/workspace/GPT-SoVITS/test_assets/ww_encore_greeting_en.ogg"

print(f"容器路径: {container_path}")
try:
    r3 = httpx.post(f"{BASE_URL}/tts", json={
        "text": "你好",
        "text_lang": "zh",
        "ref_audio_path": container_path,
        "prompt_text": "",
        "prompt_lang": "zh",
        "text_split_method": "cut5",
        "batch_size": 1,
        "media_type": "wav",
        "streaming_mode": False,
    }, timeout=60)
    print(f"路径模式 → {r3.status_code}")
    if r3.status_code == 200:
        print(f"✅ 成功！音频大小: {len(r3.content)} bytes")
        Path("/tmp/test_path_mode.wav").write_bytes(r3.content)
        print("已保存到 /tmp/test_path_mode.wav")
    else:
        print(f"❌ 失败: {r3.text[:500]}")
except Exception as e:
    print(f"异常: {e}")

# ── 4. 测试 multipart 模式 ────────────────────────────────────
print("\n=== 4. 测试 multipart 模式（form-data + audio_file）===")
try:
    audio_bytes = ref_audio.read_bytes()
    mime = "audio/ogg" if ref_audio.suffix == ".ogg" else "audio/wav"
    r4 = httpx.post(f"{BASE_URL}/tts",
        data={
            "text": "你好",
            "text_lang": "zh",
            "prompt_text": "",
            "prompt_lang": "zh",
            "text_split_method": "cut5",
            "batch_size": "1",
            "media_type": "wav",
            "streaming_mode": "false",
        },
        files={"audio_file": (ref_audio.name, audio_bytes, mime)},
        timeout=60,
    )
    print(f"multipart 模式 → {r4.status_code}")
    if r4.status_code == 200:
        print(f"✅ 成功！音频大小: {len(r4.content)} bytes")
        Path("/tmp/test_multipart.wav").write_bytes(r4.content)
        print("已保存到 /tmp/test_multipart.wav")
    else:
        print(f"❌ 失败: {r4.text[:500]}")
except Exception as e:
    print(f"异常: {e}")

# ── 5. 查看 /tts 接口文档 ─────────────────────────────────────
print("\n=== 5. 查看 /tts 接口签名 ===")
try:
    r5 = httpx.get(f"{BASE_URL}/openapi.json", timeout=5)
    if r5.status_code == 200:
        import json
        spec = r5.json()
        tts_spec = spec.get("paths", {}).get("/tts", {})
        print(json.dumps(tts_spec, ensure_ascii=False, indent=2)[:2000])
    else:
        print(f"GET /openapi.json → {r5.status_code}")
except Exception as e:
    print(f"异常: {e}")
