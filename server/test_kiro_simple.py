from kiro_parser import KiroParser, find_most_recent_kiro_session

# Probar con la sesión más reciente disponible
test_file = find_most_recent_kiro_session()

print(f"Testing file: {test_file}")
print(f"Exists: {bool(test_file and test_file.exists())}")

if test_file and test_file.exists():
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
