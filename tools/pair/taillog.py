import json, socket, sys
n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
s = socket.create_connection(("127.0.0.1", 17999), timeout=30)
s.sendall((json.dumps({"method": "engine:logs", "params": {"engine": "lmstudio"}, "timeout": 20}) + "\n").encode())
buf = b""
while not buf.endswith(b"\n"):
    c = s.recv(65536)
    if not c: break
    buf += c
lines = (json.loads(buf).get("result") or {}).get("lines", [])
for l in lines[-n:]:
    print("  ", (l.get("text") if isinstance(l, dict) else str(l))[:200])
