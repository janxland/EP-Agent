"""
Tool 4: renderer
输入: ABC Notation 字符串 + 元数据
输出: 完整 abcjs HTML 文件（可在浏览器直接打开）
"""
import os


def render_abcjs_html(abc_str: str, output_path: str,
                      title: str = "",
                      show_source: bool = True) -> str:
    """
    ABC Notation → abcjs 渲染 HTML

    Args:
        abc_str:     完整 ABC 字符串
        output_path: 输出 HTML 文件路径
        title:       页面标题（默认从 ABC T: 字段提取）
        show_source: 是否展示 ABC 源码区域

    Returns:
        output_path
    """
    # 从 ABC 提取标题
    if not title:
        for line in abc_str.splitlines():
            if line.startswith("T:"):
                title = line[2:].strip()
                break
        if not title:
            title = "乐谱"

    # ABC 转义（用于嵌入 JS 字符串）
    abc_escaped = abc_str.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")

    source_block = ""
    if show_source:
        source_block = """
  <div class="source-card">
    <div class="source-header">
      <span><i class="fa fa-code"></i> ABC Notation 源码</span>
      <button id="btn-copy"><i class="fa fa-copy"></i> 复制</button>
    </div>
    <pre id="abc-src"></pre>
  </div>"""

    source_js = ""
    if show_source:
        source_js = """
  document.getElementById('abc-src').textContent = ABC;
  document.getElementById('btn-copy').addEventListener('click', function() {
    navigator.clipboard.writeText(ABC).then(() => {
      this.textContent = '✓ 已复制';
      setTimeout(() => { this.innerHTML = '<i class=\"fa fa-copy\"></i> 复制'; }, 2000);
    });
  });"""

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{title}</title>
<script src="https://cdn.tailwindcss.com/3.4.17"></script>
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.7.2/css/all.min.css"/>
<script src="https://cdn.jsdelivr.net/npm/abcjs@6.4.4/dist/abcjs-basic-min.js"></script>
<style>
  body {{ margin:0; background:#f8fafc; font-family:'Helvetica Neue',sans-serif; }}
  .wrap {{ max-width:960px; margin:0 auto; padding:32px 20px 60px; }}
  .header {{ background:linear-gradient(135deg,#1a3a5c,#2a6fa8); color:#fff;
             border-radius:16px; padding:28px 32px; margin-bottom:20px; }}
  .header h1 {{ margin:0 0 6px; font-size:28px; }}
  .header p  {{ margin:0; opacity:.7; font-size:14px; }}
  .controls {{ background:#fff; border-radius:12px; padding:14px 20px;
               display:flex; gap:14px; align-items:center; flex-wrap:wrap;
               box-shadow:0 2px 12px rgba(0,0,0,.07); margin-bottom:18px; }}
  .btn {{ border:none; border-radius:8px; padding:8px 18px; cursor:pointer;
          font-size:14px; display:flex; align-items:center; gap:6px; transition:.15s; }}
  .btn-play {{ background:#2a6fa8; color:#fff; box-shadow:0 2px 8px rgba(42,111,168,.3); }}
  .btn-play:hover {{ background:#1a5a8a; }}
  .btn-stop {{ background:#eee; color:#555; }}
  .btn-stop:hover {{ background:#ddd; }}
  .ctrl-label {{ font-size:12px; color:#999; }}
  input[type=range] {{ width:90px; accent-color:#2a6fa8; }}
  .tempo-val {{ font-size:13px; color:#1a3a5c; font-weight:600; min-width:52px; }}
  .score-card {{ background:#fff; border-radius:14px; padding:28px 20px;
                 box-shadow:0 2px 16px rgba(0,0,0,.07); margin-bottom:18px; }}
  .score-card svg {{ width:100%!important; height:auto!important; }}
  .source-card {{ background:#f1f5f9; border:1px solid #e2e8f0; border-radius:12px;
                  padding:16px 20px; }}
  .source-header {{ display:flex; justify-content:space-between; align-items:center;
                    margin-bottom:10px; font-size:13px; color:#64748b; }}
  #btn-copy {{ background:#fff; border:1px solid #cbd5e1; border-radius:6px;
               padding:4px 12px; font-size:12px; cursor:pointer; color:#64748b; }}
  #btn-copy:hover {{ background:#f8fafc; }}
  pre#abc-src {{ font-family:monospace; font-size:12px; color:#334155;
                 line-height:1.7; white-space:pre-wrap; word-break:break-all;
                 margin:0; max-height:180px; overflow-y:auto; }}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <h1>{title}</h1>
    <p>由 Sky: Children of the Light 游戏谱转换 &nbsp;·&nbsp; abcjs 渲染</p>
  </div>

  <div class="controls">
    <button class="btn btn-play" id="btn-play"><i class="fa fa-play"></i> 播放</button>
    <button class="btn btn-stop" id="btn-stop"><i class="fa fa-stop"></i> 停止</button>
    <span class="ctrl-label">速度</span>
    <input type="range" id="tempo" min="60" max="200" value="120"/>
    <span class="tempo-val" id="tempo-val">♩=120</span>
    <span class="ctrl-label" style="margin-left:auto">缩放</span>
    <input type="range" id="zoom" min="0.6" max="2.0" step="0.1" value="1.0"/>
    <span class="tempo-val" id="zoom-val">1.0×</span>
  </div>

  <div class="score-card">
    <div id="score"></div>
  </div>
{source_block}
</div>

<script>
const ABC_TEMPLATE = `{abc_escaped}`;
let tempo = 120, zoom = 1.0;

function getABC() {{
  return ABC_TEMPLATE.replace(/Q:1\\/4=\\d+/, 'Q:1/4=' + tempo);
}}

function render() {{
  ABCJS.renderAbc('score', getABC(), {{
    responsive:'resize', scale:zoom,
    staffwidth:880, paddingleft:0, paddingright:0, add_classes:true
  }});
}}

window.addEventListener('load', () => {{
  render();
{source_js}

  document.getElementById('tempo').addEventListener('input', function() {{
    tempo = +this.value;
    document.getElementById('tempo-val').textContent = '♩=' + tempo;
    render();
  }});

  document.getElementById('zoom').addEventListener('input', function() {{
    zoom = parseFloat(this.value);
    document.getElementById('zoom-val').textContent = zoom.toFixed(1) + '×';
    render();
  }});

  document.getElementById('btn-play').addEventListener('click', () => {{
    if (!ABCJS.synth.supportsAudio()) {{
      alert('请使用 Chrome / Edge 浏览器播放'); return;
    }}
    const vis = ABCJS.renderAbc('score', getABC(), {{
      responsive:'resize', scale:zoom, staffwidth:880,
      paddingleft:0, paddingright:0, add_classes:true
    }});
    const synth = new ABCJS.synth.CreateSynth();
    synth.init({{ visualObj: vis[0], options:{{ program:0 }} }})
         .then(() => synth.prime())
         .then(() => synth.start())
         .catch(e => console.warn(e));
  }});

  document.getElementById('btn-stop').addEventListener('click', () => render());
}});
</script>
</body>
</html>"""

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)
    return output_path
