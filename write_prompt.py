import base64
import sys
import os

# Lit le b64 depuis argv[1]
b64 = sys.argv[1]
decoded = base64.b64decode(b64).decode('utf-8')
with open('C:/Users/luken/AppData/Local/Temp/prompt_match_temp.txt', 'w', encoding='utf-8') as f:
    f.write(decoded)