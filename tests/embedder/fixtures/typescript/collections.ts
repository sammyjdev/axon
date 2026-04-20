const COLLECTIONS: Record<string, { restricted: boolean }> = {
  personal:  { restricted: false },
  career:    { restricted: false },
  knowledge: { restricted: false },
  work:      { restricted: true },
};

function getSearchCollections(ctx?: string): string[] {
  if (ctx === "work") return ["work"];
  return Object.entries(COLLECTIONS)
    .filter(([, meta]) => !meta.restricted)
    .map(([name]) => name);
}

function collectionName(ctx: string): string {
  return `prometheus_${ctx}`;
}

function isRestricted(ctx: string): boolean {
  return COLLECTIONS[ctx]?.restricted ?? false;
}

export { getSearchCollections, collectionName, isRestricted };
