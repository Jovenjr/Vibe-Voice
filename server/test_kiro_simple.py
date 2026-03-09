import os
from pathlib import Path
from kiro_parser import KiroParser

# Probar con un archivo específico
history_dir = Path(os.environ['APPDATA']) / 'Kiro' / 'User' / 'History'
test_file = history_dir / '-1012eccc' / '96JX.json'

print(f"Testing file: {test_file}")
print(f"Exists: {test_file.exists()}")

if test_file.exists():
    parser = KiroParser()
    data = parser.parse_file(test_file)
    
    if data:
        print(f"\nSession ID: {data['session_id']}")
        print(f"Messages: {len(data['messages'])}")
        
        msgs = parser.get_all_messages(data)
        print(f"Parsed messages: {len(msgs)}")
        
        for i, msg in enumerate(msgs[:3]):
            print(f"\n{i+1}. [{msg.role}]")
            print(f"   {msg.content[:100]}")
