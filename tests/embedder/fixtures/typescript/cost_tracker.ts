interface CostEntry {
  model: string;
  inputTokens: number;
  outputTokens: number;
  costUsd: number;
  timestamp: string;
}

class CostTracker {
  private entries: CostEntry[] = [];

  record(model: string, inputTokens: number, outputTokens: number): void {
    const costUsd = this.estimateCost(model, inputTokens, outputTokens);
    this.entries.push({
      model,
      inputTokens,
      outputTokens,
      costUsd,
      timestamp: new Date().toISOString(),
    });
  }

  todayTotal(): number {
    const today = new Date().toISOString().slice(0, 10);
    return this.entries
      .filter((e) => e.timestamp.startsWith(today))
      .reduce((sum, e) => sum + e.costUsd, 0);
  }

  weekTotal(): number {
    const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
    return this.entries
      .filter((e) => e.timestamp >= cutoff)
      .reduce((sum, e) => sum + e.costUsd, 0);
  }

  private estimateCost(model: string, inputTokens: number, outputTokens: number): number {
    const rates: Record<string, [number, number]> = {
      "claude-haiku-4-5-20251001": [0.00025, 0.00125],
      "claude-sonnet-4-6":         [0.003,   0.015],
      "claude-opus-4-7":           [0.015,   0.075],
    };
    const [inRate, outRate] = rates[model] ?? [0.001, 0.003];
    return (inputTokens * inRate + outputTokens * outRate) / 1000;
  }
}

export { CostTracker, CostEntry };
