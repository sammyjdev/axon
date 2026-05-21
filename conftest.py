import sys
from pathlib import Path

# Adiciona src/ ao path para que pytest encontre o pacote axon sem instalação
sys.path.insert(0, str(Path(__file__).parent / "src"))
