/**
 * GPT-SoVITS 安装器 · Node.js 后端服务
 * 负责：安装/启动/停止/更新/状态检测 + 实时推送日志（SSE）
 *
 * 启动: node server.js
 * 前端: http://localhost:3333
 */

const http    = require('http');
const fs      = require('fs');
const path    = require('path');
const { spawn, exec } = require('child_process');
const os      = require('os');

const PORT    = 3333;
const IS_WIN  = process.platform === 'win32';
const IS_MAC  = process.platform === 'darwin';

// ── 全局状态 ──────────────────────────────────────────────────
const appState = {
  webuiPid:     null,   // 当前 WebUI 子进程 PID
  webuiRunning: false,  // WebUI 是否在运行
  installDir:   null,   // 最后一次安装/使用的目录（持久化到 state.json）
};

const STATE_FILE = path.join(__dirname, 'state.json');

function saveState() {
  try { fs.writeFileSync(STATE_FILE, JSON.stringify(appState, null, 2)); } catch (_) {}
}
function loadState() {
  try {
    const s = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    Object.assign(appState, s);
    appState.webuiRunning = false; // 重启 server 时重置进程状态
    appState.webuiPid = null;
  } catch (_) {}
}
loadState();

// ── SSE 客户端列表 ────────────────────────────────────────────
const sseClients = new Set();

function broadcast(type, data) {
  const payload = `data: ${JSON.stringify({ type, ...data })}\n\n`;
  for (const res of sseClients) {
    try { res.write(payload); } catch (_) { sseClients.delete(res); }
  }
}

// ── 命令执行器（实时流式输出）────────────────────────────────
function runCommand(cmd, args, cwd, onLine) {
  return new Promise((resolve, reject) => {
    const opts = { cwd: cwd || process.cwd(), shell: true, env: { ...process.env } };
    const child = spawn(cmd, args, opts);

    child.stdout.on('data', d => {
      d.toString().split('\n').filter(Boolean).forEach(line => {
        onLine && onLine('info', line);
        broadcast('log', { level: 'info', msg: line });
      });
    });
    child.stderr.on('data', d => {
      d.toString().split('\n').filter(Boolean).forEach(line => {
        const level = line.toLowerCase().includes('error') ? 'error' : 'warn';
        onLine && onLine(level, line);
        broadcast('log', { level, msg: line });
      });
    });
    child.on('close', code => {
      if (code === 0) resolve(code);
      else reject(new Error(`Exit code ${code}`));
    });
    child.on('error', reject);
  });
}

// ── CUDA 版本检测 ────────────────────────────────────────────
// 返回: { raw, version, device }
//   raw     — nvidia-smi 输出的原始 CUDA Version 字符串，如 "12.8"
//   version — 数字，如 12.8
//   device  — 推荐的 --Device 参数: 'CU128' | 'CU126' | 'CPU'
function detectCuda() {
  return new Promise(resolve => {
    exec('nvidia-smi', (err, stdout) => {
      if (err) { resolve({ raw: null, version: null, device: 'CPU' }); return; }
      // nvidia-smi 输出示例: "CUDA Version: 12.8"
      const m = stdout.match(/CUDA Version:\s*([\d.]+)/);
      if (!m) { resolve({ raw: null, version: null, device: 'CPU' }); return; }
      const raw = m[1];
      const ver = parseFloat(raw);
      let device = 'CPU';
      if      (ver >= 12.8) device = 'CU128';
      else if (ver >= 12.6) device = 'CU126';
      resolve({ raw, version: ver, device });
    });
  });
}

// ── 环境检测 ─────────────────────────────────────────────────
async function detectEnv() {
  const results = {};
  const checks = [
    { key: 'python', cmd: IS_WIN ? 'python --version' : 'python3 --version' },
    { key: 'conda',  cmd: 'conda --version' },
    { key: 'git',    cmd: 'git --version' },
    { key: 'ffmpeg', cmd: 'ffmpeg -version' },
  ];
  const [, cuda] = await Promise.all([
    Promise.all(checks.map(({ key, cmd }) =>
      new Promise(resolve => {
        exec(cmd, (err, stdout, stderr) => {
          const out = (stdout + stderr).trim().split('\n')[0];
          results[key] = err ? null : out;
          resolve();
        });
      })
    )),
    detectCuda(),
  ]);
  return {
    os:      process.platform,
    arch:    os.arch(),
    cuda,
    results,
  };
}

// ── 安装步骤执行器 ───────────────────────────────────────────
async function runInstall(config) {
  const { device = 'CPU', source = 'HF-Mirror', installDir, dlUVR5 } = config;
  const dir = installDir || './GPT-SoVITS';
  const absDir = path.resolve(dir);
  const uvr5Flag = dlUVR5 ? (IS_WIN ? '--DownloadUVR5' : '--download-uvr5') : '';

  const steps = [
    {
      id: 'detect', title: '环境检测',
      run: async () => {
        const env = await detectEnv();
        broadcast('env', env);
        broadcast('log', { level: 'success', msg: `OS: ${env.os} ${env.arch}` });
        Object.entries(env.results).forEach(([k, v]) => {
          broadcast('log', { level: v ? 'success' : 'warn', msg: `  ${k}: ${v || '未安装'}` });
        });
        // CUDA 检测结果播报
        if (env.cuda && env.cuda.raw) {
          broadcast('log', { level: 'success', msg: `  CUDA: ${env.cuda.raw} → 推荐设备: ${env.cuda.device}` });
          // 如果前端没有手动指定 GPU，自动覆盖为检测到的最优设备
          if (config.device === 'CPU' && env.cuda.device !== 'CPU') {
            broadcast('log', { level: 'highlight', msg: `  [自动] 检测到 CUDA ${env.cuda.raw}，已切换为 ${env.cuda.device}` });
            config.device = env.cuda.device;
            broadcast('cuda_detected', { raw: env.cuda.raw, device: env.cuda.device });
          }
        } else {
          broadcast('log', { level: 'warn', msg: '  CUDA: 未检测到 NVIDIA GPU，使用 CPU 模式' });
        }
      }
    },
    {
      id: 'deps', title: '安装系统依赖',
      run: async () => {
        if (IS_MAC) {
          broadcast('log', { level: 'cmd', msg: '$ brew install ffmpeg' });
          await runCommand('brew', ['install', 'ffmpeg'], null).catch(() =>
            broadcast('log', { level: 'warn', msg: 'brew install ffmpeg 失败，请手动安装' })
          );
        } else if (!IS_WIN) {
          broadcast('log', { level: 'cmd', msg: '$ sudo apt-get install -y ffmpeg libsox-dev' });
          await runCommand('sudo', ['apt-get', 'install', '-y', 'ffmpeg', 'libsox-dev'], null).catch(() =>
            broadcast('log', { level: 'warn', msg: 'apt 安装失败，请手动安装 ffmpeg' })
          );
        } else {
          broadcast('log', { level: 'info', msg: 'Windows: 检查 FFmpeg...' });
        }
      }
    },
    {
      id: 'conda_env', title: '创建 Conda 环境',
      run: async () => {
        broadcast('log', { level: 'cmd', msg: '$ conda create -n GPTSoVits python=3.10 -y' });
        await runCommand('conda', ['create', '-n', 'GPTSoVits', 'python=3.10', '-y'], null);
      }
    },
    {
      id: 'clone', title: '克隆仓库',
      run: async () => {
        if (fs.existsSync(path.join(absDir, '.git'))) {
          broadcast('log', { level: 'warn', msg: `目录已存在，执行 git pull...` });
          broadcast('log', { level: 'cmd', msg: `$ git -C ${dir} pull` });
          await runCommand('git', ['-C', absDir, 'pull'], null);
        } else {
          broadcast('log', { level: 'cmd', msg: `$ git clone --depth=1 https://github.com/RVC-Boss/GPT-SoVITS ${dir}` });
          await runCommand('git', ['clone', '--depth=1', 'https://github.com/RVC-Boss/GPT-SoVITS', absDir], null);
        }
      }
    },
    {
      id: 'install', title: '安装 Python 依赖',
      run: async () => {
        if (IS_WIN) {
          await runWinInstall({ absDir, device, source, uvr5Flag });
        } else {
          const devLower = device.toLowerCase();
          const args = ['run', '-n', 'GPTSoVits', 'bash', 'install.sh',
            '--device', devLower, '--source', source];
          if (uvr5Flag) args.push(uvr5Flag);
          broadcast('log', { level: 'cmd', msg: `$ conda ${args.join(' ')}` });
          await runCommand('conda', args, absDir);
        }
      }
    },
    {
      id: 'verify', title: '验证安装',
      run: async () => {
        broadcast('log', { level: 'cmd', msg: "$ conda run -n GPTSoVits python -c 'import torch; print(torch.__version__)'" });
        await runCommand('conda', ['run', '--no-capture-output', '-n', 'GPTSoVits', 'python', '-c',
          "import torch; print('PyTorch:', torch.__version__)"], absDir);
        // 检查模型目录
        const modelDir = path.join(absDir, 'GPT_SoVITS', 'pretrained_models');
        if (fs.existsSync(modelDir)) {
          const files = fs.readdirSync(modelDir);
          broadcast('log', { level: 'success', msg: `预训练模型目录: ${files.length} 个文件 ✓` });
        } else {
          broadcast('log', { level: 'warn', msg: '预训练模型目录不存在，首次启动时将自动下载' });
        }
      }
    },
  ];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    broadcast('step', { idx: i, status: 'running', title: step.title });
    broadcast('log', { level: 'highlight', msg: `\n▶ [${i+1}/${steps.length}] ${step.title}` });
    try {
      await step.run();
      broadcast('step', { idx: i, status: 'done', title: step.title });
      broadcast('log', { level: 'success', msg: `✓ ${step.title} 完成` });
    } catch (err) {
      broadcast('step', { idx: i, status: 'error', title: step.title });
      broadcast('log', { level: 'error', msg: `✗ ${step.title} 失败: ${err.message}` });
      broadcast('install_error', { step: i, message: err.message });
      return;
    }
    broadcast('progress', { pct: Math.round((i + 1) / steps.length * 100) });
  }

  broadcast('install_done', { installDir: absDir });
}

// ── Windows 安装辅助：生成临时 bat 调用 PowerShell ─────────────
// conda run 无法调用 PowerShell，改用 cmd /c 写临时 bat 执行
async function runWinInstall({ absDir, device, source, uvr5Flag }) {
  // 防止调用方传入 undefined，提供安全默认值
  device  = device  || 'CPU';
  source  = source  || 'HF-Mirror';

  const psExe = await new Promise(resolve => {
    exec('pwsh --version', err => resolve(err ? 'powershell' : 'pwsh'));
  });
  broadcast('log', { level: 'info', msg: '[INFO] PowerShell: ' + psExe });

  const uvr5Arg = uvr5Flag ? ' -DownloadUVR5' : '';

  // 获取 conda base 路径
  const condaBase = await new Promise(resolve => {
    exec('conda info --base', (err, stdout) => resolve(err ? '' : stdout.trim()));
  });
  if (!condaBase) throw new Error('无法获取 conda base 路径，请确认 conda 已正确安装');
  broadcast('log', { level: 'info', msg: '  conda base: ' + condaBase });

  const hookPath = condaBase + '\\shell\\condabin\\conda-hook.ps1';

  // 写临时 wrapper.ps1：先 init conda hook，再调用 install.ps1
  // 用独立 ps1 文件彻底避免 -Command 字符串里引号嵌套导致的参数解析错位
  const psLines = [];
  psLines.push('Set-Location -LiteralPath "' + absDir + '"');
  psLines.push('if (Test-Path "' + hookPath + '") {');
  psLines.push('  . "' + hookPath + '"');
  psLines.push('  conda activate GPTSoVits');
  psLines.push('}');
  psLines.push('$ErrorActionPreference = "Stop"');
  psLines.push('& "./install.ps1" -Device ' + device + ' -Source ' + source + uvr5Arg);

  const wrapperPath = path.join(absDir, '_ep_wrapper.ps1');
  fs.writeFileSync(wrapperPath, psLines.join('\r\n'), 'utf8');

  // 写 bat：用 activate.bat 激活 cmd 层，再调用 wrapper.ps1
  const batLines = [];
  batLines.push('@echo off');
  batLines.push('chcp 65001 >nul');
  batLines.push('call "' + condaBase + '\\Scripts\\activate.bat" GPTSoVits');
  batLines.push('if %errorlevel% neq 0 ( echo [ERROR] conda activate 失败 & exit /b 1 )');
  batLines.push(psExe + ' -ExecutionPolicy Bypass -NoProfile -File "' + wrapperPath + '"');
  batLines.push('exit /b %errorlevel%');

  const batPath = path.join(absDir, '_ep_install_tmp.bat');
  fs.writeFileSync(batPath, batLines.join('\r\n'), 'utf8');

  broadcast('log', { level: 'cmd', msg: '$ cmd /c "' + batPath + '"' });

  try {
    await runCommand('cmd /c "' + batPath + '"', [], absDir);
  } finally {
    try { fs.unlinkSync(batPath); } catch(_) {}
    try { fs.unlinkSync(wrapperPath); } catch(_) {}
  }
}

// ── 检测 GPT-SoVITS 是否已安装 ──────────────────────────────
function checkInstalled(dir) {
  const absDir = path.resolve(dir || appState.installDir || './GPT-SoVITS');
  const hasGit     = fs.existsSync(path.join(absDir, '.git'));
  const hasWebui   = fs.existsSync(path.join(absDir, 'webui.py'));
  const modelDir   = path.join(absDir, 'GPT_SoVITS', 'pretrained_models');
  const hasModels  = fs.existsSync(modelDir);
  const modelCount = hasModels ? fs.readdirSync(modelDir).length : 0;
  return { installed: hasGit && hasWebui, hasModels, modelCount, absDir };
}

// ── 检测 WebUI 是否在线 ──────────────────────────────────────
// ── 检测端口是否在线 ────────────────────────────────────────
function checkPort(port) {
  return new Promise(resolve => {
    const req = http.request({ hostname: 'localhost', port, path: '/', timeout: 2000 }, r => {
      resolve(r.statusCode < 500);
    });
    req.on('error', () => resolve(false));
    req.on('timeout', () => { req.destroy(); resolve(false); });
    req.end();
  });
}
async function checkWebuiOnline() {
  return checkPort(9872);
}

// ── Kill 占用指定端口的进程 ──────────────────────────────────
function killPort(port) {
  return new Promise(resolve => {
    if (IS_WIN) {
      // Windows: netstat 找 PID，再 taskkill
      exec('netstat -ano | findstr :' + port, (err, stdout) => {
        if (err || !stdout.trim()) { resolve(); return; }
        const pids = new Set();
        stdout.trim().split('\n').forEach(line => {
          const parts = line.trim().split(/\s+/);
          const pid = parts[parts.length - 1];
          if (pid && /^\d+$/.test(pid) && pid !== '0') pids.add(pid);
        });
        if (pids.size === 0) { resolve(); return; }
        let done = 0;
        pids.forEach(pid => {
          broadcast('log', { level: 'warn', msg: '  [Kill] 端口 ' + port + ' 被 PID ' + pid + ' 占用，强制终止...' });
          exec('taskkill /PID ' + pid + ' /F', () => { if (++done === pids.size) resolve(); });
        });
      });
    } else {
      // Mac/Linux: lsof 找 PID，再 kill -9
      exec('lsof -ti :' + port, (err, stdout) => {
        if (err || !stdout.trim()) { resolve(); return; }
        const pids = stdout.trim().split('\n').filter(p => /^\d+$/.test(p));
        if (pids.length === 0) { resolve(); return; }
        let done = 0;
        pids.forEach(pid => {
          broadcast('log', { level: 'warn', msg: '  [Kill] 端口 ' + port + ' 被 PID ' + pid + ' 占用，强制终止...' });
          exec('kill -9 ' + pid, () => { if (++done === pids.length) resolve(); });
        });
      });
    }
  });
}

// ── 更新流程 ─────────────────────────────────────────────────
async function runUpdate(config) {
  const { installDir, device, source, dlUVR5 } = config;
  const absDir = path.resolve(installDir || appState.installDir || './GPT-SoVITS');
  const uvr5Flag = dlUVR5 ? (IS_WIN ? '--DownloadUVR5' : '--download-uvr5') : '';

  const steps = [
    {
      id: 'pull', title: '拉取最新代码',
      run: async () => {
        broadcast('log', { level: 'cmd', msg: `$ git -C ${absDir} pull` });
        await runCommand('git', ['-C', absDir, 'fetch', '--all'], null);
        await runCommand('git', ['-C', absDir, 'reset', '--hard', 'origin/main'], null);
      }
    },
    {
      id: 'update_deps', title: '更新 Python 依赖',
      run: async () => {
        const dev = device || 'CPU';
        const src = source || 'HF-Mirror';
        if (IS_WIN) {
          // 复用 runWinInstall，避免 conda run + PowerShell 的兼容问题
          await runWinInstall({ absDir, device: dev, source: src, uvr5Flag });
        } else {
          const args = ['run', '-n', 'GPTSoVits', 'bash', 'install.sh',
            '--device', dev.toLowerCase(), '--source', src];
          if (uvr5Flag) args.push(uvr5Flag);
          broadcast('log', { level: 'cmd', msg: '$ conda ' + args.join(' ') });
          await runCommand('conda', args, absDir);
        }
      }
    },
    {
      id: 'verify', title: '验证更新',
      run: async () => {
        await runCommand('conda', ['run', '--no-capture-output', '-n', 'GPTSoVits', 'python', '-c',
          "import torch; print('PyTorch:', torch.__version__)"], absDir);
        // 获取最新 commit
        await new Promise(resolve => {
          exec(`git -C "${absDir}" log --oneline -3`, (err, stdout) => {
            if (!err) broadcast('log', { level: 'success', msg: `最新提交:\n${stdout.trim()}` });
            resolve();
          });
        });
      }
    },
  ];

  for (let i = 0; i < steps.length; i++) {
    const step = steps[i];
    broadcast('update_step', { idx: i, status: 'running', title: step.title });
    broadcast('log', { level: 'highlight', msg: `\n▶ [${i+1}/${steps.length}] ${step.title}` });
    try {
      await step.run();
      broadcast('update_step', { idx: i, status: 'done', title: step.title });
      broadcast('log', { level: 'success', msg: `✓ ${step.title} 完成` });
    } catch (err) {
      broadcast('update_step', { idx: i, status: 'error', title: step.title });
      broadcast('log', { level: 'error', msg: `✗ ${step.title} 失败: ${err.message}` });
      broadcast('update_error', { step: i, message: err.message });
      return;
    }
    broadcast('update_progress', { pct: Math.round((i + 1) / steps.length * 100) });
  }
  broadcast('update_done', { installDir: absDir });
}

// ── HTTP 路由 ─────────────────────────────────────────────────
const server = http.createServer(async (req, res) => {
  const url = req.url.split('?')[0];

  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') { res.writeHead(204); res.end(); return; }

  // ── 静态 HTML 页面路由 ───────────────────────────────────
  const pageMap = {
    '/':          'index.html',
    '/install':   'install.html',
    '/launcher':  'launcher.html',
    '/updater':   'updater.html',
  };
  if (req.method === 'GET' && pageMap[url]) {
    const file = path.join(__dirname, pageMap[url]);
    if (fs.existsSync(file)) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(fs.readFileSync(file, 'utf8'));
    } else {
      // 回退到 index.html
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(fs.readFileSync(path.join(__dirname, 'index.html'), 'utf8'));
    }
    return;
  }

  // ── GET /events → SSE ────────────────────────────────────
  if (req.method === 'GET' && url === '/events') {
    res.writeHead(200, {
      'Content-Type':  'text/event-stream',
      'Cache-Control': 'no-cache',
      'Connection':    'keep-alive',
    });
    res.write('data: {"type":"connected"}\n\n');
    sseClients.add(res);
    req.on('close', () => sseClients.delete(res));
    return;
  }

  // ── GET /detect → 完整环境检测 ──────────────────────────
  if (req.method === 'GET' && url === '/detect') {
    const env = await detectEnv();
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(env)); return;
  }

  // ── GET /status → 安装状态 + WebUI 在线状态 ─────────────
  if (req.method === 'GET' && url === '/status') {
    const dir = new URL(req.url, `http://localhost`).searchParams.get('dir') || appState.installDir || './GPT-SoVITS';
    const inst = checkInstalled(dir);
    const online = await checkWebuiOnline();
    if (inst.installed) {
      appState.installDir = inst.absDir;
      saveState();
    }
    // 获取 git 最新 commit
    let gitLog = '';
    if (inst.installed) {
      gitLog = await new Promise(resolve => {
        exec(`git -C "${inst.absDir}" log --oneline -1`, (err, stdout) => resolve(err ? '' : stdout.trim()));
      });
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({
      installed:    inst.installed,
      hasModels:    inst.hasModels,
      modelCount:   inst.modelCount,
      installDir:   inst.absDir,
      webuiRunning: appState.webuiRunning,
      webuiOnline:  online,
      webuiPid:     appState.webuiPid,
      gitLog,
    }));
    return;
  }

  // ── POST /install → 开始安装 ─────────────────────────────
  if (req.method === 'POST' && url === '/install') {
    let body = '';
    req.on('data', d => body += d);
    req.on('end', () => {
      let config = {};
      try { config = JSON.parse(body); } catch (_) {}
      if (config.installDir) { appState.installDir = path.resolve(config.installDir); saveState(); }
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, msg: '安装已启动' }));
      runInstall(config).catch(err =>
        broadcast('install_error', { message: err.message })
      );
    });
    return;
  }

  // ── POST /launch → 启动 WebUI ────────────────────────────
  if (req.method === 'POST' && url === '/launch') {
    let body = '';
    req.on('data', d => body += d);
    req.on('end', async () => {
      let cfg = {};
      try { cfg = JSON.parse(body || '{}'); } catch (_) {}
      const installDir = cfg.installDir || appState.installDir || './GPT-SoVITS';
      const absDir = path.resolve(installDir);

      if (appState.webuiRunning) {
        res.writeHead(200, { 'Content-Type': 'application/json' });
        res.end(JSON.stringify({ ok: false, msg: 'WebUI 已在运行中', url: 'http://localhost:9872' }));
        return;
      }

      // 启动前先 kill 掉占用 9872 端口的进程，确保端口固定
      broadcast('log', { level: 'info', msg: '  检查端口 9872 占用情况...' });
      await killPort(9872);
      // 等待端口释放
      await new Promise(r => setTimeout(r, 800));
      const webuiArgs = [
        'run', '-n', 'GPTSoVits', 'python', 'webui.py',
        '--server_name', '0.0.0.0',
        '--server_port', '9872',
      ];
      const cmd = 'conda';
      broadcast('log', { level: 'highlight', msg: '▶ 启动 GPT-SoVITS WebUI...' });
      broadcast('log', { level: 'cmd', msg: '$ conda ' + webuiArgs.slice(1).join(' ') });
      broadcast('log', { level: 'info', msg: '  参数: --server_name 0.0.0.0 --server_port 9872' });

      const child = spawn(cmd, webuiArgs, { cwd: absDir, shell: false, detached: false });
      appState.webuiPid = child.pid;
      appState.webuiRunning = true;
      appState.webuiUrl = 'http://localhost:9872';
      broadcast('webui_status', { running: true, pid: child.pid });

      // 检测 WebUI 实际监听端口（兼容 9872/9874/9880）
      const portPattern = /Running on local URL:\s*http[s]?:\/\/[\d.]+:(\d+)/;
      child.stdout.on('data', d => {
        d.toString().split('\n').filter(Boolean).forEach(line => {
          broadcast('log', { level: 'info', msg: line });
          const m = line.match(portPattern);
          if (m) {
            const port = m[1];
            appState.webuiUrl = 'http://localhost:' + port;
            broadcast('webui_ready', { url: appState.webuiUrl });
            broadcast('log', { level: 'success', msg: '✓ WebUI 已就绪 → ' + appState.webuiUrl });
          }
        });
      });
      child.stderr.on('data', d => {
        d.toString().split('\n').filter(Boolean).forEach(line => {
          // 过滤掉 jinja2 unhashable dict 的 traceback 噪音，仅记录关键错误
          if (line.includes('unhashable type') || line.includes('TemplateResponse')) return;
          broadcast('log', { level: line.toLowerCase().includes('error') ? 'error' : 'warn', msg: line });
        });
      });
      child.on('close', code => {
        appState.webuiRunning = false;
        appState.webuiPid = null;
        broadcast('webui_status', { running: false, code });
        broadcast('log', { level: code === 0 ? 'info' : 'warn', msg: 'WebUI 进程已退出 (code: ' + code + ')' });
      });

      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, url: 'http://localhost:9872', pid: child.pid }));
    });
    return;
  }

  // ── POST /stop → 停止 WebUI ──────────────────────────────
  if (req.method === 'POST' && url === '/stop') {
    if (!appState.webuiRunning || !appState.webuiPid) {
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: false, msg: 'WebUI 未在运行' }));
      return;
    }
    try {
      if (IS_WIN) exec(`taskkill /PID ${appState.webuiPid} /T /F`);
      else process.kill(appState.webuiPid, 'SIGTERM');
      appState.webuiRunning = false;
      appState.webuiPid = null;
      broadcast('webui_status', { running: false });
      broadcast('log', { level: 'warn', msg: '⏹ WebUI 已停止' });
    } catch (e) {
      broadcast('log', { level: 'error', msg: `停止失败: ${e.message}` });
    }
    res.writeHead(200, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  // ── POST /update → 拉取更新 ──────────────────────────────
  if (req.method === 'POST' && url === '/update') {
    let body = '';
    req.on('data', d => body += d);
    req.on('end', () => {
      let config = {};
      try { config = JSON.parse(body); } catch (_) {}
      config.installDir = config.installDir || appState.installDir || './GPT-SoVITS';
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, msg: '更新已启动' }));
      runUpdate(config).catch(err =>
        broadcast('update_error', { message: err.message })
      );
    });
    return;
  }

  // ── POST /reinstall → 重新安装依赖 ──────────────────────
  if (req.method === 'POST' && url === '/reinstall') {
    let body = '';
    req.on('data', d => body += d);
    req.on('end', () => {
      let config = {};
      try { config = JSON.parse(body); } catch (_) {}
      config.installDir = config.installDir || appState.installDir || './GPT-SoVITS';
      res.writeHead(200, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ ok: true, msg: '重装已启动' }));
      runInstall(config).catch(err =>
        broadcast('install_error', { message: err.message })
      );
    });
    return;
  }

  res.writeHead(404); res.end('Not Found');
});

server.listen(PORT, () => {
  console.log(`\n╔═══════════════════════════════════════════╗`);
  console.log(`║  GPT-SoVITS 安装器后端服务已启动          ║`);
  console.log(`║  http://localhost:${PORT}                   ║`);
  console.log(`╚═══════════════════════════════════════════╝\n`);
  // 自动打开浏览器
  const open = IS_WIN ? 'start' : IS_MAC ? 'open' : 'xdg-open';
  exec(`${open} http://localhost:${PORT}`);
});
