"""Check performance SLAs (GLYPH latency, etc)."""

from typing import Dict, Any


class PerformanceCheck:
    """Check GLYPH graph build latency and cache hit rate."""

    def check_glyph_graph_build_latency(self) -> Dict[str, Any]:
        """GLYPH graph build should be < 500ms for typical repos."""
        try:
            from axon.context.graph_source import get_cache_stats
            stats = get_cache_stats()
            last_ms = stats.get("last_build_ms", 0.0)
            if last_ms == 0.0:
                return {
                    "status": "ok",
                    "value": None,
                    "target_ms": 500,
                    "severity": "info",
                    "note": "Cache implemented; latency recorded after first build",
                }
            return {
                "status": "ok" if last_ms < 500 else "warning",
                "value": round(last_ms, 1),
                "target_ms": 500,
                "severity": "info",
            }
        except (ImportError, AttributeError):
            return {
                "status": "unknown",
                "value": None,
                "target_ms": 500,
                "severity": "info",
                "note": "Requires instrumentation in AXON _build_glyph_graph()",
            }

    def check_glyph_cache_hit_rate(self) -> Dict[str, Any]:
        """GLYPH cache should achieve > 80% hit rate."""
        try:
            from axon.context.graph_source import get_cache_stats
            stats = get_cache_stats()
            hits = stats.get("hits", 0.0)
            misses = stats.get("misses", 0.0)
            total = hits + misses
            if total == 0:
                return {
                    "status": "ok",
                    "value": "cache_implemented_no_runtime_data",
                    "target_percent": 80,
                    "severity": "info",
                    "note": "Cache implemented; hit rate available after first requests",
                }
            hit_rate = round(hits / total * 100, 1)
            return {
                "status": "ok" if hit_rate >= 80 else "warning",
                "value": hit_rate,
                "target_percent": 80,
                "severity": "info",
            }
        except (ImportError, AttributeError):
            return {
                "status": "unknown",
                "value": None,
                "target_percent": 80,
                "severity": "info",
                "note": "Requires cache implementation in AXON",
            }

    def run(self) -> Dict[str, Dict[str, Any]]:
        """Run all performance checks."""
        return {
            "glyph_graph_build_latency_ms": self.check_glyph_graph_build_latency(),
            "glyph_cache_hit_rate": self.check_glyph_cache_hit_rate(),
        }
