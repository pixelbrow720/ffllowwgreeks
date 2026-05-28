/* FlowGreeks v3.1 — page interactions
 *
 * Progressive enhancements only. Every page works with JS off; this
 * just adds the polish layer.
 *
 *   1. Spotlight cursor on .spotlight cards
 *   2. Reveal-on-scroll for .reveal / .reveal-blur / .reveal-stagger
 *   3. Char-by-char hero text reveal (replaces SplitText for portability)
 *   4. Number ticker — animates [data-ticker] elements from 0 to value
 *   5. Hyper text — scrambles text on hover
 *   6. Tracing beam — fills the left-rail beam to scroll progress
 *   7. Cmd+K palette — opens a fake command palette for the demo
 *   8. Live-flicker simulator — mutates dashboard numbers periodically
 *   9. data-h → height for .gex-bar
 */

(() => {
  'use strict';

  const $  = (sel, ctx) => (ctx || document).querySelector(sel);
  const $$ = (sel, ctx) => Array.from((ctx || document).querySelectorAll(sel));

  // ─── 1. Spotlight cursor ────────────────────────────────────────
  document.addEventListener('mousemove', (e) => {
    const t = e.target;
    if (!t || !t.closest) return;
    const card = t.closest('.spotlight');
    if (!card) return;
    const rect = card.getBoundingClientRect();
    const x = ((e.clientX - rect.left) / rect.width) * 100;
    const y = ((e.clientY - rect.top) / rect.height) * 100;
    card.style.setProperty('--mx', x + '%');
    card.style.setProperty('--my', y + '%');
  }, { passive: true });

  // ─── 2. Reveal-on-scroll ────────────────────────────────────────
  const revealTargets = $$('.reveal:not(.in), .reveal-blur:not(.in), .reveal-stagger:not(.in)');
  if (revealTargets.length && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          entry.target.classList.add('in');
          io.unobserve(entry.target);
        }
      }
    }, { rootMargin: '0px 0px -8% 0px', threshold: 0.05 });
    revealTargets.forEach((el) => io.observe(el));
  } else {
    revealTargets.forEach((el) => el.classList.add('in'));
  }

  // ─── 3. Char-by-char hero reveal ────────────────────────────────
  // Wrap each character of [data-split] in a <span class="split-char">
  // and reveal them with a 14ms stagger when the parent enters viewport.
  $$('[data-split]').forEach((el) => {
    const text = el.textContent;
    el.textContent = '';
    [...text].forEach((ch, i) => {
      const span = document.createElement('span');
      span.className = 'split-char';
      span.textContent = ch === ' ' ? ' ' : ch;
      span.style.transitionDelay = (i * 14) + 'ms';
      el.appendChild(span);
    });
  });
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          $$('.split-char', entry.target).forEach((c) => c.classList.add('in'));
          io.unobserve(entry.target);
        }
      }
    }, { threshold: 0.1 });
    $$('[data-split]').forEach((el) => io.observe(el));
  }

  // ─── 4. Number ticker ───────────────────────────────────────────
  // <span data-ticker="-3.84" data-prefix="$" data-suffix="B" data-decimals="2">
  // Animates from 0 to value when in viewport, ~900ms ease-out.
  const fmt = (n, decimals) => {
    const sign = n < 0 ? '-' : '';
    const abs = Math.abs(n);
    return sign + abs.toFixed(decimals);
  };
  const animateTicker = (el) => {
    const target = parseFloat(el.dataset.ticker);
    const decimals = parseInt(el.dataset.decimals || '0', 10);
    const prefix = el.dataset.prefix || '';
    const suffix = el.dataset.suffix || '';
    const dur = parseInt(el.dataset.dur || '900', 10);
    const start = performance.now();
    const tick = (now) => {
      const t = Math.min(1, (now - start) / dur);
      const eased = 1 - Math.pow(1 - t, 3);
      const v = target * eased;
      el.textContent = prefix + fmt(v, decimals) + suffix;
      if (t < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver((entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) {
          animateTicker(entry.target);
          io.unobserve(entry.target);
        }
      }
    }, { threshold: 0.5 });
    $$('[data-ticker]').forEach((el) => io.observe(el));
  } else {
    $$('[data-ticker]').forEach(animateTicker);
  }

  // ─── 5. Hyper text scramble ─────────────────────────────────────
  // <span data-hyper>SPX</span> → cycles random chars then settles to value
  const HYPER_CHARS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789█▓▒░';
  const scramble = (el, target) => {
    const len = target.length;
    let frame = 0;
    const total = 18;
    const tick = () => {
      let out = '';
      for (let i = 0; i < len; i++) {
        const settled = (frame / total) * len > i;
        out += settled ? target[i] : HYPER_CHARS[Math.floor(Math.random() * HYPER_CHARS.length)];
      }
      el.textContent = out;
      frame++;
      if (frame <= total) setTimeout(tick, 28);
    };
    tick();
  };
  $$('[data-hyper]').forEach((el) => {
    const target = el.textContent;
    el.dataset.target = target;
    el.addEventListener('mouseenter', () => scramble(el, target));
  });

  // ─── 6. Tracing beam (sets --beam-h on .tracing-beam to scroll %) ─
  const tracingBeams = $$('.tracing-beam');
  if (tracingBeams.length) {
    const onScroll = () => {
      const total = document.documentElement.scrollHeight - window.innerHeight;
      const pct = total > 0 ? Math.min(100, (window.scrollY / total) * 100) : 0;
      tracingBeams.forEach((b) => b.style.setProperty('--beam-h', pct + '%'));
    };
    window.addEventListener('scroll', onScroll, { passive: true });
    onScroll();
  }

  // ─── 7. Cmd+K palette ───────────────────────────────────────────
  const palette = $('#palette');
  if (palette) {
    const open = () => palette.classList.add('open');
    const close = () => palette.classList.remove('open');
    document.addEventListener('keydown', (e) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        palette.classList.toggle('open');
      } else if (e.key === 'Escape') {
        close();
      }
    });
    palette.addEventListener('click', (e) => { if (e.target === palette) close(); });
    $$('[data-open-palette]').forEach((b) => b.addEventListener('click', open));
  }

  // ─── 8. Live-flicker simulator (dashboard only) ─────────────────
  // For each [data-live] element, randomly nudge the visible value every
  // few seconds and add a flicker class so the user sees data move.
  const liveTargets = $$('[data-live]');
  if (liveTargets.length) {
    const tickLive = () => {
      const el = liveTargets[Math.floor(Math.random() * liveTargets.length)];
      const dir = Math.random() < 0.5 ? 'up' : 'down';
      el.classList.remove('flicker-up', 'flicker-down');
      void el.offsetWidth;   /* restart animation */
      el.classList.add(dir === 'up' ? 'flicker-up' : 'flicker-down');
    };
    setInterval(tickLive, 2400);
  }

  // ─── 9. gex-bar height wiring ───────────────────────────────────
  $$('.gex-bar[data-h]').forEach((el) => {
    const h = parseFloat(el.getAttribute('data-h')) || 30;
    el.style.height = h + 'px';
  });

})();
