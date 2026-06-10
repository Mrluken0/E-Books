import sys
import base64
import os

# Lit le b64 depuis la variable d'environnement PROMPT_B64
data = os.environ.get('PROMPT_B64', '')
decoded = base64.b64decode(data.strip()).decode('utf-8')
output_path = os.environ.get('PROMPT_PATH', 'C:/Users/luken/AppData/Local/Temp/prompt_match_temp.txt')
with open(output_path, 'w', encoding='utf-8') as f:
    f.write(decoded)