import { Nav } from "@/components/landing/Nav";
import { Hero } from "@/components/landing/Hero";
import { Marquee } from "@/components/landing/Marquee";
import { Manifesto } from "@/components/landing/Manifesto";
import { Modules } from "@/components/landing/Modules";
import { Pipeline } from "@/components/landing/Pipeline";
import { DashboardPreview } from "@/components/landing/DashboardPreview";
import { Pricing } from "@/components/landing/Pricing";
import { Footer } from "@/components/landing/Footer";
import { SmoothScroll } from "@/components/landing/SmoothScroll";

export default function LandingPage() {
  return (
    <SmoothScroll>
      <div className="relative">
        <Nav />
        <Hero />
        <Marquee />
        <Manifesto />
        <Modules />
        <Pipeline />
        <DashboardPreview />
        <Pricing />
        <Footer />
      </div>
    </SmoothScroll>
  );
}
