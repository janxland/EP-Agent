/**
 * EP-Agent H5 播放器内核 — 罗小黑 (LuoXiaoHei) v7.0
 * ════════════════════════════════════════════════════
 * 改进点：
 *  - 所有变量从 <script id="ep-config"> JSON 统一读取，零散注入
 *  - 视频嵌入：自动识别 B站/抖音/YouTube/通用 iframe，生成正确 embed
 *  - 动态填充 DOM（标题/meta 等），HTML 无硬编码变量
 *  - 修复萤火虫 CSS 字符串拼接 bug
 *  - 健壮性：所有 DOM 操作前检查元素存在性
 */
(function () {
  'use strict';

  /* ══════════════════════════════════════════════════════════════
     配置读取 — 唯一数据来源
  ═══════════════════════════════════════════════════════════════ */
  const CFG = (function () {
    const DEFAULTS = {
      TITLE: '乐谱', COMPOSER: '', BPM: '', KEY: '', FORMAT_LABEL: '',
      MIDI_URL: '', ABC_CONTENT: '', NIGHT_MOOD: '', CAT_EMOJI: '🐱',
      VIDEO_URL: '', VIDEO_TITLE: '', VIDEO_PLATFORM: '',
      EXTRA_HTML: '', NOTES_JSON: []
    };
    try {
      const el = document.getElementById('ep-config');
      if (!el) return DEFAULTS;
      const raw = JSON.parse(el.textContent);
      // 清理渲染引擎未替换的占位符（降级为空字符串/空数组）
      const clean = {};
      for (const [k, v] of Object.entries(raw)) {
        if (typeof v === 'string' && /^\{\{.+\}\}$/.test(v.trim())) {
          clean[k] = k === 'NOTES_JSON' ? [] : '';
        } else {
          clean[k] = v ?? DEFAULTS[k];
        }
      }
      return { ...DEFAULTS, ...clean };
    } catch (e) {
      console.warn('[EP] ep-config parse error:', e);
      return DEFAULTS;
    }
  })();

  const TITLE        = CFG.TITLE;
  const COMPOSER     = CFG.COMPOSER;
  const BPM          = CFG.BPM;
  const KEY          = CFG.KEY;
  const FORMAT_LABEL = CFG.FORMAT_LABEL;
  const MIDI_URL     = CFG.MIDI_URL;
  const ABC          = CFG.ABC_CONTENT;
  const NIGHT_MOOD   = CFG.NIGHT_MOOD;
  const VIDEO_URL    = CFG.VIDEO_URL;
  const VIDEO_TITLE  = CFG.VIDEO_TITLE;
  const VIDEO_PLATFORM = CFG.VIDEO_PLATFORM;
  const EXTRA_HTML   = CFG.EXTRA_HTML;
  const NOTES        = Array.isArray(CFG.NOTES_JSON) ? CFG.NOTES_JSON : [];

  const C1 = '#7C5CBF', C2 = '#F5A623';
  const BC = 48;

  /* ── DOM 引用 ─────────────────────────────────────────────── */
  const $ = id => document.getElementById(id);

  /* ══════════════════════════════════════════════════════════════
     DOM 填充 — 用配置数据填充所有动态文本节点
  ═══════════════════════════════════════════════════════════════ */
  function populateDOM() {
    // 页面标题
    document.title = TITLE + ' — 罗小黑 × EP-Agent';

    // 预加载屏标题
    const preTitle = $('preTitle');
    if (preTitle) preTitle.textContent = TITLE;

    // 封面区
    const coverMood = $('coverMood');
    if (coverMood) coverMood.textContent = NIGHT_MOOD;
    const coverTitle = $('coverTitle');
    if (coverTitle) coverTitle.textContent = TITLE;
    const coverComposer = $('coverComposer');
    if (coverComposer) coverComposer.textContent = COMPOSER;
    const pillFormat = $('pillFormat');
    if (pillFormat) pillFormat.textContent = FORMAT_LABEL;
    const pillBpm = $('pillBpm');
    if (pillBpm) pillBpm.textContent = BPM ? '♩ ' + BPM : '';
    const pillKey = $('pillKey');
    if (pillKey) pillKey.textContent = KEY;

    // 详情区标题
    const detailTitle = $('detailTitle');
    if (detailTitle) detailTitle.textContent = '🌙 ' + TITLE;
    const npTitle = $('npTitle');
    if (npTitle) npTitle.textContent = TITLE;

    // 乐曲信息 meta-grid
    const metaGrid = $('metaGrid');
    if (metaGrid) {
      const items = [
        { icon: '🎵', val: TITLE,        lbl: '曲名' },
        { icon: '🎤', val: COMPOSER,     lbl: '作曲者' },
        { icon: '🎼', val: KEY,          lbl: '调号' },
        { icon: '🥁', val: BPM + (BPM ? ' BPM' : ''), lbl: '速度' },
        { icon: '📁', val: FORMAT_LABEL, lbl: '格式' },
        { icon: '🌙', val: '罗小黑',     lbl: '主题风格' },
      ].filter(it => it.val);
      metaGrid.innerHTML = items.map(it => `
        <div class="meta-item">
          <div class="meta-icon" aria-hidden="true">${it.icon}</div>
          <div>
            <div class="meta-val">${escHtml(it.val)}</div>
            <div class="meta-lbl">${escHtml(it.lbl)}</div>
          </div>
        </div>`).join('');
    }

    // EXTRA_HTML 注入（允许富文本，大模型可自由扩展）
    const extraSlot = $('extraHtmlSlot');
    if (extraSlot && EXTRA_HTML) extraSlot.innerHTML = EXTRA_HTML;

    // ABC 乐谱卡
    const abcCard = $('abcCard');
    if (abcCard) abcCard.style.display = ABC ? '' : 'none';

    // 视频卡
    injectVideo();
  }

  /* ── HTML 转义（用于纯文本填充）─────────────────────────── */
  function escHtml(s) {
    return String(s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;')
      .replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  /* ══════════════════════════════════════════════════════════════
     视频嵌入 — 自动识别平台，生成正确 iframe src
     支持：B站 / 抖音 / YouTube / 通用 iframe
  ═══════════════════════════════════════════════════════════════ */
  function resolveEmbedUrl(url) {
    if (!url) return '';
    // 已经是 embed/player 地址，直接使用
    if (url.includes('player.bilibili.com') ||
        url.includes('open.douyin.com/player') ||
        url.includes('youtube.com/embed') ||
        url.includes('youtu.be/embed')) {
      return url;
    }
    // B站普通链接 → embed
    const bvMatch = url.match(/bilibili\.com\/video\/(BV\w+)/i);
    if (bvMatch) {
      return `https://player.bilibili.com/player.html?bvid=${bvMatch[1]}&page=1&high_quality=1&danmaku=0`;
    }
    const avMatch = url.match(/bilibili\.com\/video\/av(\d+)/i);
    if (avMatch) {
      return `https://player.bilibili.com/player.html?aid=${avMatch[1]}&page=1&high_quality=1&danmaku=0`;
    }
    // YouTube 普通链接 → embed
    const ytMatch = url.match(/(?:youtube\.com\/watch\?v=|youtu\.be\/)([\w-]+)/);
    if (ytMatch) {
      return `https://www.youtube.com/embed/${ytMatch[1]}?rel=0`;
    }
    // 抖音分享链接（无法自动转换，建议直接提供 open.douyin.com/player 地址）
    if (url.includes('douyin.com') || url.includes('iesdouyin.com')) {
      return url; // 直接尝试嵌入
    }
    // 其他：原样使用
    return url;
  }

  function platformIcon(platform) {
    const p = (platform || '').toLowerCase();
    if (p.includes('bilibili') || p.includes('哔哩')) return '📺';
    if (p.includes('douyin') || p.includes('抖音'))   return '🎵';
    if (p.includes('youtube'))                         return '▶️';
    return '🎬';
  }

  function injectVideo() {
    const card = $('videoCard');
    const wrap = $('videoWrap');
    const label = $('videoLabel');
    if (!card || !wrap) return;

    if (!VIDEO_URL) { card.style.display = 'none'; return; }

    const embedUrl = resolveEmbedUrl(VIDEO_URL);
    if (!embedUrl) { card.style.display = 'none'; return; }

    // 更新标签文字
    if (label) {
      const icon = platformIcon(VIDEO_PLATFORM);
      const platformText = VIDEO_PLATFORM || '视频';
      label.textContent = icon + ' ' + platformText + (VIDEO_TITLE ? ' · ' + VIDEO_TITLE : '');
    }

    // 注入 iframe（使用 sandbox 限制权限，allow-scripts/allow-same-origin 是播放必需）
    const iframe = document.createElement('iframe');
    iframe.src = embedUrl;
    iframe.title = VIDEO_TITLE || TITLE;
    iframe.allowFullscreen = true;
    iframe.allow = 'autoplay; encrypted-media; picture-in-picture';
    iframe.setAttribute('sandbox', 'allow-scripts allow-same-origin allow-presentation allow-popups');
    iframe.style.cssText = 'width:100%;aspect-ratio:16/9;border:0;border-radius:10px;display:block;';
    iframe.loading = 'lazy';
    wrap.appendChild(iframe);
    card.style.display = '';
  }

  /* ══════════════════════════════════════════════════════════════
     播放器状态机
  ═══════════════════════════════════════════════════════════════ */
  let state     = 'idle';
  let aCtx      = null;
  let volGain   = null;
  let instr     = null;
  let mPlayer   = null;
  let nodes     = [];
  let raf       = null;
  let durMs     = 0;
  let startWall = 0;
  let pausedAt  = 0;

  function getCtx() {
    if (!aCtx) {
      aCtx = new (window.AudioContext || window.webkitAudioContext)();
      volGain = aCtx.createGain();
      volGain.gain.value = parseFloat(($('volSlider') || {}).value || 0.85);
      volGain.connect(aCtx.destination);
    }
    return aCtx;
  }

  /* ── DOM 引用（播放器控件）───────────────────────────────── */
  const elProgFill  = $('progFill');
  const elProgWrap  = $('progWrap');
  const elElapsed   = $('tElapsed');
  const elTotal     = $('tTotal');
  const elBtnPlay   = $('btnPlay');
  const elWaveform  = $('waveform');
  const elNpBar     = $('nowPlayingBar');
  const elCoverText = document.querySelector('.cover-text');
  const elCatWrap   = document.querySelector('.cat-wrap');

  /* ── 工具 ─────────────────────────────────────────────────── */
  function fmt(ms) {
    const s = Math.floor(ms / 1000);
    return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0');
  }

  /* ── 波形条初始化 ─────────────────────────────────────────── */
  const bars = [];
  (function () {
    if (!elWaveform) return;
    for (let i = 0; i < BC; i++) {
      const b = document.createElement('div');
      b.className = 'wbar';
      const phase = i / BC * Math.PI * 2;
      b.style.height = Math.round(6 + Math.abs(Math.sin(phase * 1.4 + 0.6)) * 26
                                    + Math.abs(Math.sin(phase * 3.1 + 1.0)) * 8) + 'px';
      elWaveform.appendChild(b);
      bars.push(b);
    }
  })();

  let lastBarIdx = -1;
  function litBar(idx) {
    if (idx === lastBarIdx) return;
    if (lastBarIdx >= 0 && lastBarIdx < bars.length) {
      bars[lastBarIdx].classList.remove('lit');
      bars[lastBarIdx].classList.add('passed');
    }
    if (idx >= 0 && idx < bars.length) {
      bars[idx].classList.add('lit');
      bars[idx].classList.remove('passed');
    }
    lastBarIdx = idx;
  }

  function resetBars() {
    bars.forEach(b => b.className = 'wbar');
    lastBarIdx = -1;
  }

  /* ── UI 同步 ──────────────────────────────────────────────── */
  function setProgress(r) {
    r = Math.max(0, Math.min(1, r));
    if (elProgFill) elProgFill.style.width = (r * 100).toFixed(1) + '%';
    if (elElapsed)  elElapsed.textContent  = fmt(r * durMs);
    if (durMs > 0 && elTotal) elTotal.textContent = fmt(durMs);
    litBar(Math.floor(r * BC));
  }

  function syncUI() {
    if (!elBtnPlay) return;
    const playing = state === 'playing';
    const paused  = state === 'paused';
    elBtnPlay.textContent = playing ? '⏸' : '▶';
    elBtnPlay.classList.toggle('paused', paused && !playing);
    if (!playing && !paused) resetBars();
    if (elNpBar) elNpBar.classList.toggle('vis', playing);
  }

  /* ── animLoop ─────────────────────────────────────────────── */
  function animLoop() {
    function frame() {
      if (state !== 'playing' || !durMs || isDragging) {
        if (state === 'playing') raf = requestAnimationFrame(frame);
        return;
      }
      const r = Math.min((Date.now() - startWall) / durMs, 1);
      setProgress(r);
      if (r < 1) {
        raf = requestAnimationFrame(frame);
      } else {
        state = 'ended'; syncUI(); setProgress(0);
      }
    }
    if (raf) cancelAnimationFrame(raf);
    raf = requestAnimationFrame(frame);
  }

  /* ── 停止 ─────────────────────────────────────────────────── */
  function stopAll() {
    if (mPlayer) { try { mPlayer.stop(); } catch (e) {} }
    nodes.forEach(n => { try { n.stop(0); } catch (e) {} });
    nodes = [];
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    state = 'idle'; pausedAt = 0;
    syncUI(); setProgress(0);
    if (window._syncMidiViz) window._syncMidiViz(false);
  }

  /* ── 暂停 ─────────────────────────────────────────────────── */
  function pause() {
    if (state !== 'playing') return;
    pausedAt = Date.now() - startWall;
    if (mPlayer) { try { mPlayer.pause(); } catch (e) {} }
    if (raf) { cancelAnimationFrame(raf); raf = null; }
    state = 'paused'; syncUI();
    if (window._syncMidiViz) window._syncMidiViz(false);
  }

  /* ── 恢复 ─────────────────────────────────────────────────── */
  function resume() {
    if (state !== 'paused') return;
    const ctx = getCtx();
    if (ctx.state === 'suspended') ctx.resume();
    if (mPlayer) {
      try { mPlayer.skipToSeconds(pausedAt / 1000); mPlayer.play(); }
      catch (e) { playMidi(pausedAt); return; }
      startWall = Date.now() - pausedAt;
      state = 'playing'; syncUI(); animLoop();
    } else {
      play(pausedAt);
    }
  }

  /* ── MIDI 播放 ────────────────────────────────────────────── */
  function playMidi(fromMs) {
    fromMs = fromMs || 0;
    stopAll(); state = 'loading';
    const ctx = getCtx();
    if (ctx.state === 'suspended') ctx.resume();

    function go(inst) {
      instr = inst;
      mPlayer = new MidiPlayer.Player(ev => {
        if (ev.name === 'Note on' && ev.velocity > 0 && instr) {
          instr.play(ev.noteName, ctx.currentTime,
            { gain: (ev.velocity / 127) * (volGain ? volGain.gain.value : 0.85) });
        }
        if (ev.name === 'end of track') { state = 'ended'; syncUI(); setProgress(0); }
      });
      fetch(MIDI_URL)
        .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.arrayBuffer(); })
        .then(buf => {
          mPlayer.loadArrayBuffer(buf);
          durMs = Math.round((mPlayer.getSongTime() || 0) * 1000);
          if (elTotal) elTotal.textContent = durMs > 0 ? fmt(durMs) : '—';
          if (fromMs > 0) mPlayer.skipToSeconds(fromMs / 1000);
          mPlayer.play();
          startWall = Date.now() - fromMs;
          state = 'playing'; syncUI(); animLoop();
          if (window._syncMidiViz) window._syncMidiViz(true, fromMs);
        })
        .catch(err => {
          console.warn('[EP] MIDI fetch failed, fallback:', err);
          playFallback(fromMs);
        });
    }

    if (instr) { go(instr); return; }
    Soundfont.instrument(ctx, 'acoustic_grand_piano', {
      soundfont: 'MusyngKite',
      nameToUrl: (n, sf, f) =>
        'https://cdn.jsdelivr.net/gh/gleitz/midi-js-soundfonts@gh-pages/' +
        sf + '/' + n + '-' + (f || 'mp3') + '.js'
    }).then(go).catch(() => { state = 'idle'; syncUI(); });
  }

  /* ── Fallback 振荡器 ──────────────────────────────────────── */
  function playFallback(fromMs) {
    fromMs = fromMs || 0;
    if (!NOTES.length) { state = 'idle'; syncUI(); return; }
    stopAll();
    const ctx = getCtx();
    if (ctx.state === 'suspended') ctx.resume();
    const now = ctx.currentTime;
    durMs = NOTES.reduce((m, n) => Math.max(m, (n.time_ms || 0) + (n.duration_ms || 200)), 0);
    if (elTotal) elTotal.textContent = durMs > 0 ? fmt(durMs) : '—';
    const vol = volGain ? volGain.gain.value : 0.85;
    const offset = fromMs / 1000;
    NOTES.forEach(note => {
      const t = (note.time_ms || 0) / 1000 - offset;
      if (t + (note.duration_ms || 200) / 1000 < 0) return;
      const d = (note.duration_ms || 200) / 1000;
      const start = Math.max(0, t);
      const osc = ctx.createOscillator(), g = ctx.createGain();
      osc.connect(g); g.connect(volGain || ctx.destination);
      osc.type = 'sine';
      osc.frequency.setValueAtTime(
        note.midi ? 440 * Math.pow(2, (note.midi - 69) / 12) : 440,
        now + start
      );
      g.gain.setValueAtTime(0, now + start);
      g.gain.linearRampToValueAtTime(vol * 0.14, now + start + 0.012);
      g.gain.exponentialRampToValueAtTime(0.001, now + start + d);
      osc.start(now + start); osc.stop(now + start + d + 0.06);
      nodes.push(osc);
    });
    startWall = Date.now() - fromMs;
    state = 'playing'; syncUI(); animLoop();
    if (window._syncMidiViz) window._syncMidiViz(true, fromMs);
  }

  function play(fromMs) {
    if (MIDI_URL) playMidi(fromMs || 0);
    else playFallback(fromMs || 0);
  }

  /* ── 按钮事件 ─────────────────────────────────────────────── */
  if (elBtnPlay) elBtnPlay.addEventListener('click', () => {
    if (state === 'playing') pause();
    else if (state === 'paused') resume();
    else play();
  });
  const btnStop = $('btnStop');
  if (btnStop) btnStop.addEventListener('click', stopAll);
  const btnPrev = $('btnPrev');
  if (btnPrev) btnPrev.addEventListener('click', () => { stopAll(); setTimeout(() => play(0), 60); });

  /* ── 音量 ─────────────────────────────────────────────────── */
  document.addEventListener('input', e => {
    if (e.target.id !== 'volSlider' || !volGain) return;
    volGain.gain.value = parseFloat(e.target.value);
    const icon = document.querySelector('.vol-icon');
    if (icon) icon.textContent = parseFloat(e.target.value) < 0.05 ? '🔇' : '🔈';
  });

  /* ── 进度条拖拽 ───────────────────────────────────────────── */
  let isDragging = false, dragR = 0, stateBeforeDrag = null;

  function clampRatio(clientX) {
    if (!elProgWrap) return 0;
    const rect = elProgWrap.getBoundingClientRect();
    return Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
  }

  function onDragStart(clientX) {
    isDragging = true;
    stateBeforeDrag = state;
    if (state === 'playing') {
      pausedAt = Date.now() - startWall;
      if (mPlayer) { try { mPlayer.pause(); } catch (e) {} }
      if (raf) { cancelAnimationFrame(raf); raf = null; }
    }
    dragR = clampRatio(clientX);
    if (elProgFill) elProgFill.style.transition = 'none';
    setProgress(dragR);
    if (elProgWrap) elProgWrap.classList.add('dragging');
    document.body.style.userSelect = 'none';
  }

  function onDragMove(clientX) {
    if (!isDragging) return;
    dragR = clampRatio(clientX);
    setProgress(dragR);
  }

  function onDragEnd() {
    if (!isDragging) return;
    isDragging = false;
    if (elProgFill) elProgFill.style.transition = '';
    if (elProgWrap) elProgWrap.classList.remove('dragging');
    document.body.style.userSelect = '';
    const targetMs = dragR * durMs;
    pausedAt = targetMs;
    if (stateBeforeDrag === 'playing') {
      if (mPlayer) {
        try { mPlayer.skipToSeconds(targetMs / 1000); mPlayer.play(); }
        catch (e) { play(targetMs); return; }
        startWall = Date.now() - targetMs;
        state = 'playing'; syncUI(); animLoop();
      } else { play(targetMs); }
    } else if (stateBeforeDrag === 'paused') {
      state = 'paused'; syncUI();
    } else if (durMs > 0) {
      play(targetMs);
    }
  }

  if (elProgWrap) {
    elProgWrap.addEventListener('mousedown', e => { e.preventDefault(); onDragStart(e.clientX); });
    elProgWrap.addEventListener('touchstart', e => {
      e.preventDefault(); onDragStart(e.touches[0].clientX);
    }, { passive: false });
  }
  document.addEventListener('mousemove', e => { if (isDragging) onDragMove(e.clientX); });
  document.addEventListener('mouseup',   () => { if (isDragging) onDragEnd(); });
  document.addEventListener('touchmove', e => {
    if (isDragging) { e.preventDefault(); onDragMove(e.touches[0].clientX); }
  }, { passive: false });
  document.addEventListener('touchend', () => { if (isDragging) onDragEnd(); });

  /* ── 下滑视差 ─────────────────────────────────────────────── */
  const coverScreen  = $('coverScreen');
  const detailScreen = $('detailScreen');

  function onScroll() {
    const scrollY = window.scrollY;
    const vh = window.innerHeight;
    if (scrollY < vh) {
      const p = scrollY / vh;
      const scale   = 1 - p * 0.06;
      const opacity = 1 - p * 1.4;
      const ty = -p * 30;
      if (elCatWrap)   elCatWrap.style.transform  = `translateY(${ty * 0.6}px) scale(${scale})`;
      if (elCoverText) elCoverText.style.transform = `translateY(${ty}px)`;
      if (elCoverText) elCoverText.style.opacity   = Math.max(0, opacity).toFixed(3);
    }
    const backBtn = $('backBtn');
    if (backBtn) backBtn.classList.toggle('vis', scrollY > vh * 0.3);
    if (scrollY >= vh && detailScreen) {
      const dp = (scrollY - vh) / (detailScreen.offsetHeight - vh);
      const pct = Math.max(0, Math.min(1, dp)) * 100;
      const ind = $('scrollIndicator');
      if (ind) ind.style.width = pct + '%';
    }
  }

  window.addEventListener('scroll', onScroll, { passive: true });

  /* ── 详情屏滚动进度指示线 ─────────────────────────────────── */
  (function injectScrollIndicator() {
    if (!detailScreen) return;
    const bar = document.createElement('div');
    bar.id = 'scrollIndicatorWrap';
    bar.innerHTML = '<div id="scrollIndicator"></div>';
    detailScreen.prepend(bar);
  })();

  /* ── 卡片入场动画 ─────────────────────────────────────────── */
  function initCards() {
    const cards = document.querySelectorAll('[data-card]');
    if (!('IntersectionObserver' in window)) {
      cards.forEach(el => el.classList.add('vis')); return;
    }
    const obs = new IntersectionObserver(entries => {
      entries.forEach(entry => {
        if (!entry.isIntersecting) return;
        const idx = parseInt(entry.target.dataset.cardIdx || '0');
        setTimeout(() => entry.target.classList.add('vis'), idx * 90);
        obs.unobserve(entry.target);
      });
    }, { threshold: 0.1 });
    cards.forEach((el, i) => { el.dataset.cardIdx = i; obs.observe(el); });
  }

  /* ── 返回 & 分享 ──────────────────────────────────────────── */
  const backBtn = $('backBtn');
  if (backBtn) backBtn.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));

  const shareBtn = $('shareBtn');
  if (shareBtn) shareBtn.addEventListener('click', () => {
    const lbl = shareBtn.querySelector('span:last-child');
    if (navigator.share) {
      navigator.share({ title: document.title, url: location.href }).catch(() => {});
    } else {
      navigator.clipboard.writeText(location.href).then(() => {
        if (lbl) { lbl.textContent = '✓ 链接已复制'; setTimeout(() => lbl.textContent = '分享这首乐曲', 2000); }
      }).catch(() => {});
    }
  });

  /* ── 星空（~20fps，降低 CPU 占用）────────────────────────── */
  function initStars() {
    const cv = $('starCanvas');
    if (!cv) return;
    const ctx = cv.getContext('2d');
    let W, H, stars = [];
    function resize() { W = cv.width = window.innerWidth; H = cv.height = window.innerHeight; }
    resize();
    window.addEventListener('resize', resize, { passive: true });
    for (let i = 0; i < 100; i++) {
      stars.push({ x: Math.random(), y: Math.random(),
        r: Math.random() * 1.2 + 0.2, a: Math.random(),
        s: Math.random() * 0.006 + 0.002, d: Math.random() > 0.5 ? 1 : -1 });
    }
    function drawStars() {
      ctx.clearRect(0, 0, W, H);
      stars.forEach(s => {
        s.a += s.s * s.d;
        if (s.a >= 1 || s.a <= 0) s.d *= -1;
        ctx.beginPath();
        ctx.arc(s.x * W, s.y * H, s.r, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(232,213,163,${(s.a * 0.75).toFixed(2)})`;
        ctx.fill();
      });
      setTimeout(() => requestAnimationFrame(drawStars), 50);
    }
    drawStars();
  }

  /* ── 萤火虫（CSS animation，GPU 合成，零重排）────────────── */
  function initFireflies() {
    const container = $('fireflyCanvas');
    if (!container) return;
    container.style.cssText = 'position:absolute;inset:0;overflow:hidden;pointer-events:none;';
    const colors = ['rgba(124,92,191,', 'rgba(245,166,35,'];
    for (let i = 0; i < 12; i++) {
      const dot  = document.createElement('div');
      const size = (Math.random() * 3 + 2).toFixed(1);
      const x    = (Math.random() * 90 + 5).toFixed(1);
      const y    = (Math.random() * 80 + 5).toFixed(1);
      const dur  = (Math.random() * 6 + 5).toFixed(1);
      const del  = (Math.random() * 4).toFixed(1);
      const col  = colors[i % 2];
      // 修复：正确拼接 rgba 字符串，无多余 } 和 ;
      dot.style.cssText = [
        'position:absolute',
        `left:${x}%`,
        `top:${y}%`,
        `width:${size}px`,
        `height:${size}px`,
        'border-radius:50%',
        `background:${col}0.85)`,
        `box-shadow:0 0 ${parseFloat(size) * 3}px ${col}0.5)`,
        `animation:flyFloat ${dur}s ${del}s ease-in-out infinite alternate`,
        'will-change:transform,opacity',
      ].join(';');
      container.appendChild(dot);
    }
  }

  /* ── html-midi-player 可视化注入 ─────────────────────────── */
  function initMidiViz() {
    const wrap        = $('midiVizWrap');
    const placeholder = $('midiVizPlaceholder');
    const card        = $('midiVizCard');
    if (!wrap) return;
    if (!MIDI_URL) { if (card) card.style.display = 'none'; return; }

    function tryInject() {
      if (!customElements.get('midi-player')) { setTimeout(tryInject, 400); return; }
      if (placeholder) placeholder.remove();

      const viz = document.createElement('midi-visualizer');
      viz.setAttribute('type', 'waterfall');
      viz.id = 'midiViz';
      viz.style.cssText = 'width:100%;height:200px;display:block;border-radius:10px;overflow:hidden;background:rgba(124,92,191,0.04);';

      const mp = document.createElement('midi-player');
      mp.setAttribute('src', MIDI_URL);
      mp.setAttribute('sound-font', '');
      mp.setAttribute('visualizer', '#midiViz');
      mp.style.display = 'none';
      mp.id = 'htmlMidiPlayer';

      wrap.appendChild(viz);
      wrap.appendChild(mp);

      function applyConfig() {
        try {
          viz.config = {
            noteHeight: 6, pixelsPerTimeStep: 40, noteSpacing: 1,
            activeNoteRGB: '245,166,35', inactiveNoteRGB: '124,92,191'
          };
        } catch (e) {}
      }
      viz.addEventListener('load', applyConfig);
      setTimeout(applyConfig, 1500);

      window._syncMidiViz = function (isPlaying, fromMs) {
        try {
          if (isPlaying) { mp.currentTime = (fromMs || 0) / 1000; mp.start(); }
          else { mp.stop(); }
        } catch (e) {}
      };
    }
    tryInject();
  }

  /* ── ABC 渲染 ─────────────────────────────────────────────── */
  function renderAbc() {
    const el = $('abcContainer');
    if (!el || !ABC) return;
    if (window.ABCJS) {
      try { ABCJS.renderAbc('abcContainer', ABC, { responsive: 'resize' }); } catch (e) {}
    } else {
      // ABCJS 未加载时等待重试
      setTimeout(renderAbc, 500);
    }
  }

  /* ── 预加载进度条 ─────────────────────────────────────────── */
  const preFill = $('preFill');
  let pct = 0;
  const tk = setInterval(() => {
    pct += Math.random() * 14 + 4;
    if (pct >= 90) { clearInterval(tk); pct = 90; }
    if (preFill) preFill.style.width = pct + '%';
  }, 60);

  /* ══════════════════════════════════════════════════════════════
     初始化入口
  ═══════════════════════════════════════════════════════════════ */
  // DOM 填充立即执行（无需等 load）
  populateDOM();

  window.addEventListener('load', () => {
    clearInterval(tk);
    if (preFill) preFill.style.width = '100%';
    initStars();
    initFireflies();
    initMidiViz();
    renderAbc();
    setTimeout(() => {
      const preloader = $('preloader');
      if (preloader) preloader.classList.add('out');
      setTimeout(() => {
        if (preloader) preloader.style.display = 'none';
        initCards();
      }, 800);
    }, 300);
  });

}());
