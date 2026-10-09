import { useEffect, useState } from "react";
import { API } from "@/api";

export type RealEsrganProgress = {
  percent: number;
  phase: string;
  busy: boolean;
  message: string;
};

/**
 * Poll host Real-ESRGAN progress while ``active``. Used by episode/unit upscale CTAs.
 */
export function useRealEsrganProgress(active: boolean): RealEsrganProgress | null {
  const [progress, setProgress] = useState<RealEsrganProgress | null>(null);

  useEffect(() => {
    if (!active) {
      setProgress(null);
      return;
    }
    let cancelled = false;

    const tick = async () => {
      try {
        const raw = await API.getRealEsrganProgress();
        if (cancelled) return;
        setProgress({
          percent: Math.max(0, Math.min(100, Number(raw.percent) || 0)),
          phase: String(raw.phase || ""),
          busy: Boolean(raw.busy),
          message: String(raw.message || ""),
        });
      } catch {
        // Progress is best-effort; the blocking upscale request carries the real error.
      }
    };

    void tick();
    const id = window.setInterval(() => void tick(), 1500);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [active]);

  return progress;
}
