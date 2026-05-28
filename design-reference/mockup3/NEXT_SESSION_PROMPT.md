# Prompt for next Claude Code session — mockup3 revamp

> Copy-paste this as the first message in a fresh session.

---

Halo. Lanjutkan revamp mockup3 FlowGreeks ke "premium tier" v3.1.

Sebelum apapun, baca berurutan:

1. `flowgreeks-mockup3/HANDOFF.md` — state lengkap, apa yang udah jadi, apa yang belum
2. `flowgreeks-mockup3/_v3.css` — design tokens v3.1 (Tailwind-modern palette, lamp, glare, beam, tracing-beam, statusbar, kbd, gradient-text, split-char)
3. `flowgreeks-mockup3/_v3.js` — interaction hooks: number ticker, hyper-text, char-split reveal, tracing beam scroll, Cmd+K palette, live-flicker

**Konteks singkat:**

- v3 mockup udah ada (4 page) tapi user bilang masih "lumayan" — pengen feel **premium** kayak Linear / Mercury / Bloomberg Terminal, not generic SaaS
- Design system v3.1 (`_v3.css` + `_v3.js`) udah di-rewrite dengan komponen baru: lamp hero, glare cards, beam borders, tracing-beam scroll, statusbar, Cmd+K, number-ticker, hyper-text scramble
- HTML pages BELUM di-rewrite ke v3.1 — itu kerjaan utamanya
- User minta: scroll-trigger storytelling di landing, fully-functional terminal feel di dashboard, animated bg yang restrained tapi wah

**Ground rules (durable, jangan ditanya ulang):**

- B. Indonesia buat chat casual, English buat code + comments
- Desktop only — jangan pernah saranin mobile/responsive
- Color discipline ketat: 3 earned accents (red short-γ / emerald long-γ / amber warn). Indigo + violet decorative-only di hero ambient
- Tickers locked: SPX + NDX
- Tabular numerics dengan `tnum + ss01 + cv11` always-on
- No build step. Static HTML/CSS/JS. GSAP CDN OK kalau IntersectionObserver kurang smooth
- Autonomous mode: gas aja sampai kelar, jangan minta izin per-step

**Yang gue mau lo lakuin sekarang:**

Kasih ringkasan 2-3 baris state mockup3, terus konfirm urutan kerja:

1. **`landing.html`** — biggest visual lift (lamp hero + char-split title + number-ticker KPIs + glare cards + tracing-beam pipeline section)
2. **`dashboard.html`** — terminal-grade (statusbar, Cmd+K palette, beam border on active panel, live-flicker numerics)
3. **`simulator.html` + `activate.html` + `index.html`** — port colors + sprinkle new components
4. **`DESIGN_SYSTEM.md`** — rewrite to document v3.1

Kalau direction-nya udah cocok, gas kerjain `landing.html` dulu sampai kelar baru lanjut yang lain.
