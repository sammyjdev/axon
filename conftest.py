import sys
from pathlib import Path

# Adiciona src/ ao path para que pytest encontre os pacotes em src/ sem instalação
sys.path.insert(0, str(Path(__file__).parent / "src"))
