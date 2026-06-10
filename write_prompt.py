import base64
from pathlib import Path

b64 = Path('C:/Users/luken/AppData/Local/Temp/b64_temp.txt').read_text(encoding='utf-8').strip()
decoded = base64.b64decode(b64).decode('utf-8')
Path('C:/Users/luken/AppData/Local/Temp/prompt_match_temp.txt').write_text(decoded, encoding='utf-8')