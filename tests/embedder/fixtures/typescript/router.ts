type ModelTier = "haiku" | "sonnet" | "opus" | "local";

interface RoutingDecision {
  model: ModelTier;
  reason: string;
  estimatedCostUsd: number;
}

function classifyQuery(query: string): "trivial" | "code" | "architecture" {
  if (query.length < 50) return "trivial";
  const archKeywords = ["design", "architecture", "pattern", "trade-off"];
  if (archKeywords.some((k) => query.toLowerCase().includes(k))) return "architecture";
  return "code";
}

function route(query: string, dailySpent: number, budget: number): RoutingDecision {
  if (dailySpent >= budget) {
    return { model: "haiku", reason: "budget_exceeded", estimatedCostUsd: 0.001 };
  }

  const type = classifyQuery(query);

  switch (type) {
    case "trivial":
      return { model: "haiku", reason: "trivial_query", estimatedCostUsd: 0.001 };
    case "architecture":
      return { model: "opus", reason: "architecture_query", estimatedCostUsd: 0.05 };
    default:
      return { model: "sonnet", reason: "code_analysis", estimatedCostUsd: 0.01 };
  }
}

export { route, classifyQuery, RoutingDecision, ModelTier };
