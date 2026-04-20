interface SearchResult {
  id: string;
  score: number;
  payload: Record<string, unknown>;
}

class ApiClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string, apiKey: string) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
  }

  async search(query: string, ctx?: string): Promise<SearchResult[]> {
    const resp = await fetch(`${this.baseUrl}/search`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Api-Key": this.apiKey },
      body: JSON.stringify({ query, ctx }),
    });
    if (!resp.ok) throw new Error(`Search failed: ${resp.status}`);
    return resp.json();
  }

  async saveAdr(project: string, title: string, decision: string): Promise<void> {
    await fetch(`${this.baseUrl}/adr`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Api-Key": this.apiKey },
      body: JSON.stringify({ project, title, decision }),
    });
  }

  async getMemory(query: string, ctx?: string): Promise<string[]> {
    const resp = await fetch(`${this.baseUrl}/memory?q=${encodeURIComponent(query)}&ctx=${ctx ?? ""}`);
    return resp.json();
  }
}

export { ApiClient, SearchResult };
