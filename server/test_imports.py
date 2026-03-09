from file_watcher import CopilotChatWatcher
from jsonl_parser import SUPPORTED_IDES

print('IDEs soportados:')
for k, v in SUPPORTED_IDES.items():
    print(f'  - {k}: {v["name"]}')

print('\n✓ Imports exitosos')
