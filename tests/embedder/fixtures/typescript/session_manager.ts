interface SessionEntry {
  project: string;
  summary: string;
  rawTurns: number;
  createdAt: string;
}

class SessionManager {
  private entries: SessionEntry[] = [];
  private currentProject: string;

  constructor(project: string) {
    this.currentProject = project;
  }

  addEntry(summary: string, rawTurns: number): void {
    this.entries.push({
      project: this.currentProject,
      summary,
      rawTurns,
      createdAt: new Date().toISOString(),
    });
  }

  getRecent(limit: number = 3): SessionEntry[] {
    return this.entries
      .filter((e) => e.project === this.currentProject)
      .slice(-limit)
      .reverse();
  }

  formatSummary(): string {
    const recent = this.getRecent(1);
    if (recent.length === 0) return "No session history.";
    const entry = recent[0];
    return `[${entry.createdAt} — ${entry.rawTurns} turns]\n${entry.summary}`;
  }

  setProject(project: string): void {
    this.currentProject = project;
  }
}

export { SessionManager, SessionEntry };
