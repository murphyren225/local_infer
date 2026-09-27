"""Run PAIR's nvpair-ui-broker over stdio and expose its JSON-RPC on a local TCP port.

  python3 pairctl.py serve          # long-running: spawns the broker, relays RPC on 127.0.0.1:17999
  python3 pairctl.py call METHOD [PARAMS_JSON] [--timeout S]

Notifications from the broker are appended to ~/pair/events.jsonl; broker stderr goes to ~/pair/broker.log.
"""
import json, os, socket, subprocess, sys, threading, time
from pathlib import Path

HOME = Path.home() / "pair"
BIN = Path(os.environ.get("PAIR_BIN", HOME / "Personal-AI-Router" / "services" / "build" / "bin"))
PORT = int(os.environ.get("PAIR_PORT", "17999"))


def serve():
    tag = os.environ.get("PAIR_TAG", "")
    log = open(HOME / f"broker{tag}.log", "ab")
    events = open(HOME / f"events{tag}.jsonl", "a")
    env = {**os.environ, "NVPAIR_LOG_LEVEL": os.environ.get("NVPAIR_LOG_LEVEL", "info")}
    proc = subprocess.Popen([str(BIN / "nvpair-ui-broker")], cwd=str(BIN), stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=log, env=env, text=True, bufsize=1)
    pending, lock, ready = {}, threading.Lock(), threading.Event()

    def reader():
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if "id" in msg and ("result" in msg or "error" in msg):
                with lock:
                    ev = pending.get(msg["id"])
                if ev:
                    ev[1] = msg; ev[0].set()
            else:
                if msg.get("method") == "app:ready":
                    ready.set()
                events.write(json.dumps({"t": time.time(), **msg}) + "\n"); events.flush()
        ready.set()

    threading.Thread(target=reader, daemon=True).start()
    next_id = [1]

    def call(method, params, timeout):
        with lock:
            rid = next_id[0]; next_id[0] += 1
            pending[rid] = [threading.Event(), None]
        req = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            req["params"] = params
        proc.stdin.write(json.dumps(req) + "\n"); proc.stdin.flush()
        ev = pending[rid][0]
        ok = ev.wait(timeout)
        with lock:
            msg = pending.pop(rid)[1]
        return msg if ok else {"error": {"message": f"timeout after {timeout}s", "method": method}}

    srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT)); srv.listen(8)
    ready.wait(60)
    print(f"broker pid {proc.pid}, app:ready={ready.is_set()}, rpc on 127.0.0.1:{PORT}", flush=True)

    def handle(conn):
        with conn:
            data = b""
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            try:
                req = json.loads(data)
                resp = call(req["method"], req.get("params"), req.get("timeout", 60))
            except Exception as exc:
                resp = {"error": {"message": str(exc)}}
            conn.sendall((json.dumps(resp) + "\n").encode())

    while proc.poll() is None:
        conn, _ = srv.accept()
        threading.Thread(target=handle, args=(conn,), daemon=True).start()
    print("broker exited", proc.returncode, flush=True)


def call_cli(method, params_json=None, timeout=60):
    s = socket.create_connection(("127.0.0.1", PORT), timeout=timeout + 5)
    req = {"method": method, "params": json.loads(params_json) if params_json else None, "timeout": timeout}
    s.sendall((json.dumps(req) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    print(json.dumps(json.loads(buf), ensure_ascii=False, indent=1)[:4000])


if __name__ == "__main__":
    if sys.argv[1:2] == ["serve"]:
        serve()
    elif sys.argv[1:2] == ["call"]:
        args = sys.argv[2:]
        timeout = 60
        if "--timeout" in args:
            i = args.index("--timeout"); timeout = float(args[i + 1]); del args[i:i + 2]
        call_cli(args[0], args[1] if len(args) > 1 else None, timeout)
    else:
        print(__doc__)
