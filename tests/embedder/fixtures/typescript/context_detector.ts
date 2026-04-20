interface DetectionResult {
  context: string;
  confidence: number;
  display: string;
}

const CONTENT_SIGNALS: Record<string, RegExp[]> = {
  knowledge: [/\bjava\b/i, /\bspring\b/i, /\bkafka\b/i, /\bllm\b/i],
  personal:  [/\baer[uo]s\b/i, /\brpg\b/i],
  career:    [/\bvaga\b/i, /\bentrevista\b/i, /\brecruiter\b/i],
  work:      [/\bavangrid\b/i, /\beks\b/i],
};

class ContextDetector {
  detect(query: string, cwd?: string): DetectionResult {
    const scores: Record<string, number> = Object.fromEntries(
      Object.keys(CONTENT_SIGNALS).map((k) => [k, 0])
    );

    for (const [ctx, patterns] of Object.entries(CONTENT_SIGNALS)) {
      const hits = patterns.filter((p) => p.test(query)).length;
      if (hits > 0) scores[ctx] = Math.min(hits / 3, 1.0);
    }

    if (cwd) {
      const cwdCtx = this.scoreCwd(cwd);
      if (cwdCtx) scores[cwdCtx] = (scores[cwdCtx] ?? 0) + 0.4;
    }

    const winner = Object.entries(scores).sort(([, a], [, b]) => b - a)[0];
    const confidence = winner ? winner[1] / (Object.values(scores).reduce((a, b) => a + b, 0) || 1) : 0;

    return {
      context: winner?.[0] ?? "general",
      confidence,
      display: `[${winner?.[0] ?? "general"} ${Math.round(confidence * 100)}%]`,
    };
  }

  private scoreCwd(cwd: string): string | null {
    if (cwd.includes("aerus-rpg") || cwd.includes("rpg-master")) return "personal";
    if (cwd.includes("avangrid")) return "work";
    if (cwd.includes("vault/knowledge")) return "knowledge";
    return null;
  }
}

export { ContextDetector, DetectionResult };
