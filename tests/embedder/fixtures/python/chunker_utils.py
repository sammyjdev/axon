from dataclasses import dataclass


@dataclass
class Chunk:
    symbol: str
    chunk_type: str
    start_line: int
    end_line: int
    content: str


def split_by_lines(text: str, chunk_size: int = 30) -> list[str]:
    lines = text.splitlines()
    return ["\n".join(lines[i : i + chunk_size]) for i in range(0, len(lines), chunk_size)]


def estimate_token_count(text: str) -> int:
    return max(1, len(text) // 4)


def is_orphan(chunk: Chunk) -> bool:
    return chunk.symbol in ("", "anonymous", "<unknown>")


class ChunkFilter:
    def __init__(self, max_lines: int = 100, min_lines: int = 2) -> None:
        self.max_lines = max_lines
        self.min_lines = min_lines

    def is_valid(self, chunk: Chunk) -> bool:
        line_count = chunk.end_line - chunk.start_line + 1
        return self.min_lines <= line_count <= self.max_lines and not is_orphan(chunk)

    def filter(self, chunks: list[Chunk]) -> list[Chunk]:
        return [c for c in chunks if self.is_valid(c)]
