/**
 * EP-Agent H5 播放器内核 — 罗小黑 (LuoXiaoHei) v4.0
 *
 * 修复点：
 *  1. 真正的 pause/resume — 记录 pausedAt 位置，resume 时 skipToSeconds 跳回
 *  2. 进度条点击跳转 — 播放中/暂停中均正确更新 startWall 基准
 *  3. 单例播放器 — stopAll 只在重置时调用，不破坏暂停状态
 */
(function () {
  'use strict';

  /* ── 数据注入 ─────────────────────────────────────────────── */
  const NOTES = (function () {
    try { return JSON.parse(document.getElementById('_nj').textContent); }
    catch (e) { return []; }
  })();
  // 优先读 index.html 注入的全局变量（已经过模板渲染，占位符已替换）
  // 兜底：直接打开 player.js 时全局变量不存在，降级为空字符串
  const ABC      = (window._EP_ABC_CONTENT && window._EP_ABC_CONTENT !== '{{ABC_CONTENT}}')
    ? window._EP_ABC_CONTENT : "";
  const MIDI_URL = (window._EP_MIDI_URL && window._EP_MIDI_URL !== '{{MIDI_URL}}')
    ? window._EP_MIDI_URL : "";
  const C1 = '#7C5CBF', C2 = '#F5A623';
  const BC = 48; // 波形条数量

  /* ── 状态机 ───────────────────────────────────────────────── */
  // state: 'idle' | 'loading' | 'playing' | 'paused' | 'ended'
  let state    = 'idle';
  let aCtx     = null;
  let volGain  = null;
  let instr    = null;
  let mPlayer  = null;
  let nodes    = [];       // fallback 振荡器节点
  let raf      = null;     // animLoop requestAnimationFrame id
  let durMs    = 0;        // 总时长 ms
  let startWall = 0;       // 播放开始时的 Date.now()（用于进度计算）
  let pausedAt  = 0;       // 暂停时已播放的 ms（resume 用）

  /* ── AudioContext ─────────────────────────────────────────── */
  function getCtx() {
    if (!aCtx) {
      aCtx = new (window.AudioContext || window.webkitAudioContext)();
      volGain = aCtx.createGain();
      volGain.gain.value = parseFloat(volSlider().value);
      volGain.connect(aCtx.destination);
    }
    return aCtx;
  }

  /* ── DOM 快捷引用 ─────────────────────────────────────────── */
  const $ = id => document.getElementById(id);
  const volSlider   = () => $('volSlider');
  const progFill    = () => $('progFill');
  const progWrap    = () => $('progWrap');
  const tElapsed    = () => $('tElapsed');
  const tTotal      = () => $('tTotal');
  const btnPlay     = () => $('btnPlay');
  const waveEl      = () => $('waveform');
  const npBar       = () => $('nowPlayingBar');

  /* ── 工具函数 ─────────────────────────────────────────────── */
  function fmt(ms) {
    const s = Math.floor(ms / 1000);
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  /* ── 波形条 ───────────────────────────────────────────────── */
  const bars = [];
  (function initWave() {
    const el = waveEl();
    if (!el) return;
    for (let i = 0; i < BC; i++) {
      const b = document.createElement('div');
      b.className = 'wbar';
      b.style.height = Math.round(6 + Math.abs(Math.sin(i * 0.45 + 0.9)) * 32) + 'px';
      el.appendChild(b);
      bars.push(b);
    }
  })();

  function litBar(idx) {
    bars.forEach((b, i) => b.classList.toggle('lit', i === idx));
  }

  /* ── UI 同步 ──────────────────────────────────────────────── */
  function setProgress(r) {
    const pct = (Math.max(0, Math.min(1, r)) * 100).toFixed(2) + '%';
    progFill().style.width = pct;
    tElapsed().textContent = fmt(r * durMs);
    if (durMs > 0) tTotal().textContent = fmt(durMs);
  }

  function syncUI() {
    const playing = state === 'playing';
    const paused  = state === 'paused';
    const btn = btnPlay();
    if (playing) {
      btn.textContent = '⏸';
      btn.classList.remove('paused');
    } else if (paused) {
      btn.textContent = '▶';
      btn.classList.add('paused');
    } else {
      btn.textContent = '▶';
      btn.classList.remove('paused');
      bars.forEach(b => b.classList.remove('lit'));
    }
    // 详情屏 Now Playing 指示条
    const nb = npBar();
    if (nb) nb.classList.toggle('vis', playing);
  }

  /* ── animLoop — 只在 playing 时推进进度 ──────────────────── */
  function animLoop() {
    function frame() {
      if (state !== 'playing' || !durMs) return;
      const elapsed = Date.now() - startWall;
      const r = Math.min(elapsed / durMs, 1);
      setProgress(r);
      litBar(Math.floor(r * BC));
      if (r < 1) {
        raf = requestAnimationFrame(frame);
      } else {
        // 自然结束
        state = 'ended';
        syncUI();
        setProgress(0);
      }
    }
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(frame);
  }

  /* ── 停止所有节点（真正重置） ─────────────────────────────── */
  function stopAll() {
    if (mPlayer) { try { mPlayer.stop(); } catch (e) {} }
    nodes.forEach(n => { try { n.stop(0); } catch (e) {} });
    nodes = [];
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    state = 'idle';
    pausedAt = 0;
    syncUI();
    setProgress(0);
  }

  /* ── 暂停（保留位置）─────────────────────────────────────── */
  function pause() {
    if (state !== 'playing') return;
    pausedAt = Date.now() - startWall;   // 记录已播放 ms
    if (mPlayer) { try { mPlayer.pause(); } catch (e) {} }
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    state = 'paused';
    syncUI();
  }

  /* ── 从暂停位置恢复 ───────────────────────────────────────── */
  function resume() {
    if (state !== 'paused') return;
    const ctx = getCtx();
    if (ctx.state === 'suspended') ctx.resume();

    if (mPlayer) {
      // MIDI 播放器：跳到暂停位置继续
      try {
        mPlayer.skipToSeconds(pausedAt / 1000);
        mPlayer.play();
      } catch (e) {
        // skipToSeconds 失败则重头播
        playMidi(); return;
      }
      startWall = Date.now() - pausedAt;
      state = 'playing';
      syncUI();
      animLoop();
    } else if (nodes.length) {
      // fallback 振荡器不支持 resume，重新从 pausedAt 开始
      playFallback(pausedAt);
    }
  }

  /* ── MIDI 播放 ────────────────────────────────────────────── */
  function playMidi(fromMs) {
    fromMs = fromMs || 0;
    stopAll();
    state = 'loading';
    const ctx = getCtx();
    if (ctx.state === 'suspended') ctx.resume();

    function go(inst) {
      instr = inst;
      mPlayer = new MidiPlayer.Player(ev => {
        if (ev.name === 'Note on' && ev.velocity > 0 && instr) {
          const v = volGain ? volGain.gain.value : 0.85;
          instr.play(ev.noteName, ctx.currentTime, { gain: (ev.velocity / 127) * v });
        }
        if (ev.name === 'end of track') {
          state = 'ended';
          syncUI();
          setProgress(0);
        }
      });

      fetch(MIDI_URL)
        .then(r => r.arrayBuffer())
        .then(buf => {
          mPlayer.loadArrayBuffer(buf);
          durMs = Math.round((mPlayer.getSongTime() || 0) * 1000);
          tTotal().textContent = durMs > 0 ? fmt(durMs) : '—';

          if (fromMs > 0) {
            mPlayer.skipToSeconds(fromMs / 1000);
          }
          mPlayer.play();
          startWall = Date.now() - fromMs;
          state = 'playing';
          syncUI();
          animLoop();
        })
        .catch(e => {
          console.warn('MIDI load failed', e);
          playFallback(fromMs);
        });
    }

    if (instr) {
      go(instr);
    } else {
      Soundfont.instrument(ctx, 'acoustic_grand_piano', {
        soundfont: 'MusyngKite',
        nameToUrl: (n, sf, f) =>
          'https://cdn.jsdelivr.net/gh/gleitz/midi-js-soundfonts@gh-pages/' +
          sf + '/' + n + '-' + (f || 'mp3') + '.js'
      }).then(go).catch(() => { state = 'idle'; syncUI(); });
    }
  }

  /* ── Fallback 振荡器播放 ──────────────────────────────────── */
  function playFallback(fromMs) {
    fromMs = fromMs || 0;
    if (!NOTES.length) { state = 'idle'; syncUI(); return; }
    stopAll();
    const ctx = getCtx();
    if (ctx.state === 'suspended') ctx.resume();
    const now = ctx.currentTime;
    durMs = NOTES.reduce((m, n) => Math.max(m, n.time_ms + (n.duration_ms || 200)), 0);
    tTotal().textContent = durMs > 0 ? fmt(durMs) : '—';
    const vol = volGain ? volGain.gain.value : 0.85;
    const offset = fromMs / 1000;

    NOTES.forEach(note => {
      const t = note.time_ms / 1000 - offset;
      if (t + (note.duration_ms || 200) / 1000 < 0) return; // 已过去的音符跳过
      const d = (note.duration_ms || 200) / 1000;
      const start = Math.max(0, t);
      const osc = ctx.createOscillator();
      const g   = ctx.createGain();
      osc.connect(g);
      g.connect(volGain || ctx.destination);
      osc.type = 'sine';
      osc.frequency.setValueAtTime(
        note.midi ? 440 * Math.pow(2, (note.midi - 69) / 12) : 440,
        now + start
      );
      g.gain.setValueAtTime(0, now + start);
      g.gain.linearRampToValueAtTime(vol * 0.14, now + start + 0.012);
      g.gain.exponentialRampToValueAtTime(0.001, now + start + d);
      osc.start(now + start);
      osc.stop(now + start + d + 0.06);
      nodes.push(osc);
    });

    startWall = Date.now() - fromMs;
    state = 'playing';
    syncUI();
    animLoop();
  }

  /* ── 主播放入口 ───────────────────────────────────────────── */
  function play(fromMs) {
    if (MIDI_URL) playMidi(fromMs || 0);
    else playFallback(fromMs || 0);
  }

  /* ── 按钮事件 ─────────────────────────────────────────────── */
  btnPlay().addEventListener('click', () => {
    if (state === 'playing') pause();
    else if (state === 'paused') resume();
    else play();
  });

  $('btnStop').addEventListener('click', stopAll);

  $('btnPrev').addEventListener('click', () => {
    stopAll();
    setTimeout(() => play(0), 60);
  });

  /* ── 音量控件 ─────────────────────────────────────────────── */
  document.addEventListener('input', e => {
    if (e.target.id === 'volSlider' && volGain) {
      volGain.gain.value = parseFloat(e.target.value);
      // 更新图标
      const icon = document.querySelector('.vol-icon');
      if (icon) icon.textContent = parseFloat(e.target.value) < 0.05 ? '🔇' : '🔈';
    }
  });

  /* ── 进度条点击跳转 ───────────────────────────────────────── */
  progWrap().addEventListener('click', function (e) {
    if (!durMs) return;
    const rect = this.getBoundingClientRect();
    const r = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    const targetMs = r * durMs;

    if (state === 'playing') {
      // 播放中：MIDI 跳转
      if (mPlayer) {
        try { mPlayer.skipToSeconds(targetMs / 1000); } catch (ex) {}
      }
      startWall = Date.now() - targetMs;  // 重置基准时间
      setProgress(r);
    } else if (state === 'paused') {
      // 暂停中：更新 pausedAt，等 resume 时跳到新位置
      pausedAt = targetMs;
      setProgress(r);
    } else {
      // idle/ended：直接从目标位置开始播
      play(targetMs);
    }
  });

  /* ── 音符可视化 ───────────────────────────────────────────── */
  function drawNotes() {
    const cv = $('notesCanvas');
    if (!cv) return;
    const W = cv.offsetWidth || 320;
    const H = cv.offsetHeight || 120;
    cv.width  = W * devicePixelRatio;
    cv.height = H * devicePixelRatio;
    const ctx2 = cv.getContext('2d');
    ctx2.scale(devicePixelRatio, devicePixelRatio);
    ctx2.clearRect(0, 0, W, H);

    if (!NOTES.length) {
      ctx2.fillStyle = 'rgba(124,92,191,0.25)';
      ctx2.font = '12px -apple-system,sans-serif';
      ctx2.textAlign = 'center';
      ctx2.textBaseline = 'middle';
      ctx2.fillText('暂无音符数据', W / 2, H / 2);
      return;
    }

    const maxT = NOTES.reduce((m, n) => Math.max(m, n.time_ms + (n.duration_ms || 200)), 1);
    const pitches = [...new Set(NOTES.map(n => n.midi || n.pitch || 60))].sort((a, b) => a - b);
    const rows = Math.max(pitches.length, 1);
    const pidx = {};
    pitches.forEach((p, i) => pidx[p] = i);
    const barH = Math.max(3, Math.min(8, Math.floor((H - 10) / rows)));

    NOTES.forEach(note => {
      const x  = (note.time_ms / maxT) * W;
      const pw = Math.max(2, ((note.duration_ms || 200) / maxT) * W);
      const pi = pidx[note.midi || note.pitch || 60] ?? 0;
      const y  = H - 8 - ((pi / Math.max(rows - 1, 1)) * (H - 16));
      ctx2.globalAlpha = 0.4 + (pi / rows) * 0.55;
      const gr = ctx2.createLinearGradient(x, y, x + pw, y);
      gr.addColorStop(0, C1);
      gr.addColorStop(1, C2);
      ctx2.fillStyle = gr;
      ctx2.beginPath();
      if (ctx2.roundRect) ctx2.roundRect(x, y, pw, barH, barH / 2);
      else ctx2.rect(x, y, pw, barH);
      ctx2.fill();
    });
    ctx2.globalAlpha = 1;
  }

  /* ── ABC 渲染 ─────────────────────────────────────────────── */
  function renderAbc() {
    const el = $('abcContainer');
    if (!el || !ABC || !window.ABCJS) return;
    try { ABCJS.renderAbc('abcContainer', ABC, { responsive: 'resize' }); }
    catch (e) {}
  }

  /* ── 卡片入场动画 ─────────────────────────────────────────── */
  function initCards() {
    if ('IntersectionObserver' in window) {
      const obs = new IntersectionObserver(entries => {
        entries.forEach(e => {
          if (e.isIntersecting) { e.target.classList.add('vis'); obs.unobserve(e.target); }
        });
      }, { threshold: 0.1 });
      document.querySelectorAll('[data-card]').forEach(el => obs.observe(el));
    } else {
      document.querySelectorAll('[data-card]').forEach(el => el.classList.add('vis'));
    }
  }

  /* ── 返回按钮 ─────────────────────────────────────────────── */
  const backBtn = $('backBtn');
  window.addEventListener('scroll', () => {
    backBtn.classList.toggle('vis', window.scrollY > window.innerHeight * 0.3);
  }, { passive: true });
  backBtn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

  /* ── 分享按钮 ─────────────────────────────────────────────── */
  $('shareBtn').addEventListener('click', () => {
    const lbl = document.querySelector('.share-btn span:last-child');
    if (navigator.share) {
      navigator.share({ title: '{{TITLE}}', url: location.href }).catch(() => {});
    } else {
      navigator.clipboard.writeText(location.href)
        .then(() => {
          lbl.textContent = '✓ 链接已复制';
          setTimeout(() => lbl.textContent = '分享这首乐曲', 2200);
        })
        .catch(() => {});
    }
  });

  /* ── 星空粒子 ─────────────────────────────────────────────── */
  function initStars() {
    const cv = $('starCanvas');
    if (!cv) return;
    const ctx = cv.getContext('2d');
    const stars = [];
    function resize() {
      cv.width  = window.innerWidth;
      cv.height = window.innerHeight;
    }
    resize();
    window.addEventListener('resize', resize, { passive: true });
    for (let i = 0; i < 120; i++) {
      stars.push({
        x: Math.random(),
        y: Math.random(),
        r: Math.random() * 1.4 + 0.3,
        a: Math.random(),
        s: Math.random() * 0.008 + 0.003,
        d: Math.random() > 0.5 ? 1 : -1
      });
    }
    function drawStars() {
      ctx.clearRect(0, 0, cv.width, cv.height);
      stars.forEach(s => {
        s.a += s.s * s.d;
        if (s.a >= 1 || s.a <= 0) s.d *= -1;
        ctx.beginPath();
        ctx.arc(s.x * cv.width, s.y * cv.height, s.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(232,213,163,${s.a * 0.7})`;
        ctx.fill();
      });
      requestAnimationFrame(drawStars);
    }
    drawStars();
  }

  /* ── 萤火虫粒子 ───────────────────────────────────────────── */
  function initFireflies() {
    const cv = $('fireflyCanvas');
    if (!cv) return;
    const ctx = cv.getContext('2d');
    const flies = [];
    function resize() {
      cv.width  = cv.offsetWidth;
      cv.height = cv.offsetHeight;
    }
    resize();
    window.addEventListener('resize', resize, { passive: true });
    for (let i = 0; i < 18; i++) {
      flies.push({
        x: Math.random() * cv.width,
        y: Math.random() * cv.height,
        vx: (Math.random() - 0.5) * 0.5,
        vy: (Math.random() - 0.5) * 0.5,
        r: Math.random() * 2.5 + 1,
        a: Math.random(),
        as: Math.random() * 0.012 + 0.005,
        ad: Math.random() > 0.5 ? 1 : -1,
        color: Math.random() > 0.5 ? '124,92,191' : '245,166,35'
      });
    }
    function drawFlies() {
      ctx.clearRect(0, 0, cv.width, cv.height);
      flies.forEach(f => {
        f.x += f.vx; f.y += f.vy;
        f.a += f.as * f.ad;
        if (f.a >= 1 || f.a <= 0.1) f.ad *= -1;
        if (f.x < 0) f.x = cv.width;
        if (f.x > cv.width) f.x = 0;
        if (f.y < 0) f.y = cv.height;
        if (f.y > cv.height) f.y = 0;
        ctx.beginPath();
        ctx.arc(f.x, f.y, f.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${f.color},${f.a * 0.8})`;
        ctx.shadowBlur = 8;
        ctx.shadowColor = `rgba(${f.color},0.6)`;
        ctx.fill();
        ctx.shadowBlur = 0;
      });
      requestAnimationFrame(drawFlies);
    }
    drawFlies();
  }

  /* ── 预加载进度条 ─────────────────────────────────────────── */
  const preFill = $('preFill');
  let pct = 0;
  const tk = setInterval(() => {
    pct += Math.random() * 12 + 3;
    if (pct >= 90) { clearInterval(tk); pct = 90; }
    preFill.style.width = pct + '%';
  }, 65);

  /* ── 初始化入口 ───────────────────────────────────────────── */
  window.addEventListener('load', () => {
    clearInterval(tk);
    preFill.style.width = '100%';
    initStars();
    initFireflies();
    drawNotes();
    renderAbc();
    setTimeout(() => {
      $('preloader').classList.add('out');
      setTimeout(() => {
        $('preloader').style.display = 'none';
        initCards();
      }, 900);
    }, 300);
  });

  window.addEventListener('resize', drawNotes, { passive: true });

}());
