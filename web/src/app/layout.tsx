import type { Metadata } from "next";
import { Inter, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const jbMono = JetBrains_Mono({
  subsets: ["latin"],
  variable: "--font-jb-mono",
  display: "swap",
});

export const metadata: Metadata = {
  title: "FlowGreeks — Read the Dealer",
  description:
    "Real-time 0DTE options flow + dealer positioning intelligence for SPX & NDX. Built for traders who want to see the forced flow before it hits.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body
        className={`${inter.variable} ${jbMono.variable} antialiased bg-bg-base text-ink-base`}
      >
        {children}
      </body>
    </html>
  );
}
