"""测试环境：把仓库根目录加进 sys.path。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
