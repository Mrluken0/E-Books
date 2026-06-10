import sys
import base64

sys.stdin.reconfigure(encoding='utf-8')
data = sys.stdin.buffer.read()
decoded = base64.b64decode(data.strip()).decode('utf-8')
with open('C:/LKN_Digital/KDP/prompt_match_temp.txt', 'w', encoding='utf-8') as f:
    f.write(decoded)