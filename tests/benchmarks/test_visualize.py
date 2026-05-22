import pytest

matplotlib = pytest.importorskip("matplotlib")

from benchmarks.visualize import render_chart


def test_render_chart_writes_png(tmp_path):
    out = render_chart(output_dir=tmp_path)
    assert out.exists()
    assert out.suffix == ".png"
    assert out.stat().st_size > 0
