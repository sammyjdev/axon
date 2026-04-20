interface SearchOptions {
  ctx?: string;
  language?: string;
  limit?: number;
}

interface AdrPayload {
  project: string;
  title: string;
  context: string;
  decision: string;
  rationale: string;
}

async function searchCode(query: string, opts: SearchOptions = {}): Promise<string> {
  const resp = await fetch("/mcp/search_code", {
    method: "POST",
    body: JSON.stringify({ query, ...opts }),
  });
  return resp.text();
}

async function saveAdr(payload: AdrPayload): Promise<string> {
  const resp = await fetch("/mcp/save_adr", {
    method: "POST",
    body: JSON.stringify(payload),
  });
  return resp.text();
}

async function getMemory(query: string, ctx?: string): Promise<string> {
  const resp = await fetch("/mcp/get_memory", {
    method: "POST",
    body: JSON.stringify({ query, ctx }),
  });
  return resp.text();
}

async function ask(query: string, cwd?: string, ctx?: string): Promise<string> {
  const resp = await fetch("/mcp/ask", {
    method: "POST",
    body: JSON.stringify({ query, cwd, ctx }),
  });
  return resp.text();
}

export { searchCode, saveAdr, getMemory, ask };
