"use client";

import { useState } from "react";
import { ArrowUpRight, KeyRound, Lock } from "lucide-react";

export function Pricing() {
  const [key, setKey] = useState("");
  const [touched, setTouched] = useState(false);

  const valid = /^fg_[a-zA-Z0-9]{16,}$/.test(key.trim());

  return (
    <section
      id="activate"
      className="relative overflow-hidden border-b border-line pt-20 pb-32"
    >
      {/* ambient lamp glow — now sits behind the activation card */}
      <div className="pointer-events-none absolute top-1/3 left-1/2 h-[420px] w-[760px] -translate-x-1/2 -translate-y-1/2 rounded-[100%] bg-brand/[0.09] blur-3xl" />
      <div className="pointer-events-none absolute inset-0 bg-grid opacity-25 [mask-image:radial-gradient(ellipse_55%_45%_at_50%_45%,black,transparent_75%)]" />

      <div className="relative mx-auto w-full max-w-[1100px] px-6 lg:px-10">
        {/* header */}
        <div className="mx-auto max-w-2xl text-center">
          <div className="inline-flex items-center gap-2 rounded-full border border-line bg-bg-card/60 px-3 py-1.5 backdrop-blur">
            <Lock className="h-3 w-3 text-brand-hi" />
            <span className="text-[11px] uppercase tracking-[0.18em] text-ink-base">
              Plug in
            </span>
          </div>

          <h2 className="mt-7 font-display text-display-lg text-ink-high">
            Activate <br />
            <span className="text-gradient-brand">your access.</span>
          </h2>

          <p className="mt-6 text-[15px] text-ink-muted leading-relaxed">
            FlowGreeks is provisioned by your{" "}
            <span className="text-ink-high">flowjob.id</span> account. Paste the
            API key minted there to plug into the live terminal — no signup, no
            password, no card form here.
          </p>
        </div>

        {/* activation card */}
        <div className="mt-14 mx-auto max-w-[720px]">
          <div className="relative rounded-2xl border border-line bg-bg-card/80 p-8 backdrop-blur-xl shadow-[0_30px_120px_-40px_rgba(255,42,91,0.45)]">
            {/* corner badge */}
            <div className="absolute -top-3 left-8 inline-flex items-center gap-1.5 rounded-full bg-brand px-2.5 py-1 text-[10px] uppercase tracking-[0.2em] text-white">
              <span className="relative flex h-1.5 w-1.5">
                <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-white opacity-75" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-white" />
              </span>
              Add-on · flowjob.id
            </div>

            <div className="flex items-center justify-between">
              <div>
                <div className="text-[10px] uppercase tracking-[0.18em] text-ink-faint">
                  API key
                </div>
                <div className="mt-1 text-[15px] text-ink-high font-medium">
                  Authorize your terminal session
                </div>
              </div>
              <KeyRound className="h-4 w-4 text-ink-muted" />
            </div>

            {/* input + cta */}
            <div className="mt-6">
              <div
                className={`group flex items-center gap-3 rounded-xl border bg-bg-base/60 px-4 py-3 transition-colors ${
                  touched && key && !valid
                    ? "border-accent-short/50"
                    : "border-line focus-within:border-brand/60"
                }`}
              >
                <span className="font-mono text-[13px] text-ink-faint select-none">
                  $
                </span>
                <input
                  type="text"
                  spellCheck={false}
                  autoComplete="off"
                  placeholder="fg_••••••••••••••••••••••••••••••"
                  value={key}
                  onChange={(e) => {
                    setKey(e.target.value);
                    setTouched(true);
                  }}
                  className="flex-1 bg-transparent font-mono text-[14px] text-ink-high placeholder-ink-ghost outline-none tabnum"
                />
                {valid && (
                  <span className="text-[10px] uppercase tracking-[0.16em] text-accent-long">
                    valid
                  </span>
                )}
              </div>

              <div className="mt-4 flex items-center justify-between gap-4">
                <div className="text-[11px] text-ink-faint">
                  Bearer · stored in httpOnly cookie · never logged
                </div>
                <button
                  disabled={!valid}
                  className={`inline-flex items-center gap-2 rounded-full px-5 py-2.5 text-sm font-medium transition-all ${
                    valid
                      ? "bg-brand text-white hover:bg-brand-hi shadow-[0_8px_32px_-12px_#ff2a5b]"
                      : "border border-line bg-bg-subtle/40 text-ink-faint cursor-not-allowed"
                  }`}
                >
                  Activate
                  <ArrowUpRight className="h-3.5 w-3.5" />
                </button>
              </div>
            </div>

            {/* divider */}
            <div className="hr-line my-7" />

            {/* secondary row */}
            <div className="flex items-center justify-between flex-wrap gap-4">
              <div className="text-[12.5px] text-ink-muted">
                Don&apos;t have a key yet?
              </div>
              <a
                href="https://flowjob.id"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1.5 text-[12.5px] text-ink-base hover:text-brand-hi transition-colors"
              >
                Manage subscription at flowjob.id
                <ArrowUpRight className="h-3 w-3" />
              </a>
            </div>
          </div>

          {/* footer note */}
          <div className="mt-6 grid grid-cols-1 sm:grid-cols-3 gap-3">
            <Note label="Auth" body="Opaque API keys · SHA-256 stored" />
            <Note label="Rate" body="Per-key budget · stored on the row" />
            <Note label="Revoke" body="Anytime, from flowjob.id settings" />
          </div>
        </div>
      </div>
    </section>
  );
}

function Note({ label, body }: { label: string; body: string }) {
  return (
    <div className="rounded-md border border-line/70 bg-bg-card/40 px-4 py-3">
      <div className="text-[10px] uppercase tracking-[0.16em] text-ink-faint">
        {label}
      </div>
      <div className="mt-1 text-[12px] text-ink-base leading-snug">{body}</div>
    </div>
  );
}
