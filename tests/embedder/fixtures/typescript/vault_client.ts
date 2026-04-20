import { readFileSync, writeFileSync, existsSync, mkdirSync } from "fs";
import { join } from "path";

interface DailyNote {
  date: string;
  content: string;
}

class VaultClient {
  private vaultPath: string;

  constructor(vaultPath: string) {
    this.vaultPath = vaultPath;
  }

  readDailyNote(date: string): DailyNote | null {
    const filePath = join(this.vaultPath, "daily", `${date}.md`);
    if (!existsSync(filePath)) return null;
    return { date, content: readFileSync(filePath, "utf-8") };
  }

  writeDailyNote(date: string, content: string): void {
    const dir = join(this.vaultPath, "daily");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, `${date}.md`), content, "utf-8");
  }

  appendToDailyNote(date: string, text: string): void {
    const existing = this.readDailyNote(date);
    const newContent = existing ? `${existing.content}\n\n${text}` : text;
    this.writeDailyNote(date, newContent);
  }

  listContextFiles(ctx: string): string[] {
    const dir = join(this.vaultPath, ctx);
    if (!existsSync(dir)) return [];
    return []; // simplified — real impl would use fs.readdirSync recursively
  }
}

export { VaultClient, DailyNote };
