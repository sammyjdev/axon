interface Chunk {
  symbol: string;
  chunkType: "method" | "function" | "class" | "interface";
  startLine: number;
  endLine: number;
  content: string;
  language: string;
}

function estimateChunkSize(source: string, maxTokens: number = 512): number {
  return Math.ceil(source.length / 4 / maxTokens);
}

class ChunkValidator {
  private readonly minLines: number;
  private readonly maxLines: number;

  constructor(minLines: number = 2, maxLines: number = 100) {
    this.minLines = minLines;
    this.maxLines = maxLines;
  }

  isValid(chunk: Chunk): boolean {
    const lineCount = chunk.endLine - chunk.startLine + 1;
    return (
      lineCount >= this.minLines &&
      lineCount <= this.maxLines &&
      chunk.symbol !== "" &&
      chunk.symbol !== "anonymous"
    );
  }

  filterValid(chunks: Chunk[]): Chunk[] {
    return chunks.filter((c) => this.isValid(c));
  }
}

export { Chunk, ChunkValidator, estimateChunkSize };
