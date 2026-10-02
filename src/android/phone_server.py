"""手机端入口：全本机运行 —— uvicorn 后端 + llama.cpp 推理，无需任何电脑。"""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

DEFAULT_CFG = """app:
  name: 无人机新规雷达
  version: mobile-1.0
  data_dir: {DATA_DIR}
location:
  city: 深圳
  province: 广东
  keep_national: true
sources:
  caac: {enabled: true}
llm:
  provider: ollama
  host: http://127.0.0.1:11434
  model: qwen2.5-0.5b
  timeout: 180
schedule: {}
notify:
  desktop: false
"""

MODEL_NAME = "qwen2.5-0.5b-instruct-q4_k_m.gguf"


def _log(root: Path, msg: str) -> None:
    try:
        with open(root / "llama.log", "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass
    print(msg)


def _ensure_model(root: Path) -> str:
    """把 APK assets 里的模型拷到可写目录（首次约 470MB）。"""
    dst = root / "models" / MODEL_NAME
    if dst.exists() and dst.stat().st_size > 300 * 1024 * 1024:
        _log(root, "model cached: %d MB" % (dst.stat().st_size // 1048576))
        return str(dst)
    try:
        from java import jclass, jarray, jbyte
        ActivityThread = jclass("android.app.ActivityThread")
        application = ActivityThread.currentApplication()
        assets = application.getAssets()
        dst.parent.mkdir(parents=True, exist_ok=True)
        ins = assets.open("models/" + MODEL_NAME)
        buf = jarray(jbyte)(1 << 20)
        total = 0
        with open(dst, "wb") as out:
            while True:
                n = ins.read(buf, 0, len(buf))
                if n <= 0:
                    break
                out.write(bytes(buf[:n]))
                total += n
        ins.close()
        _log(root, "model extracted: %d MB" % (total // 1048576))
    except Exception as e:
        _log(root, "model extract FAILED: %r" % (e,))
    return str(dst) if dst.exists() and dst.stat().st_size > 1000 else ""

def _ensure_seed_db(root: Path) -> None:
    """首次启动把 APK 里带的基线库铺好，装完就有内容。"""
    db = root / "data" / "uavwatch.sqlite3"
    if db.exists() and db.stat().st_size > 100000:
        return
    try:
        from java import jclass, jarray, jbyte
        ActivityThread = jclass("android.app.ActivityThread")
        assets = ActivityThread.currentApplication().getAssets()
        db.parent.mkdir(parents=True, exist_ok=True)
        ins = assets.open("seed/uavwatch.sqlite3")
        buf = jarray(jbyte)(1 << 20)
        total = 0
        with open(db, "wb") as out:
            while True:
                n = ins.read(buf, 0, len(buf))
                if n <= 0:
                    break
                out.write(bytes(buf[:n]))
                total += n
        ins.close()
        _log(root, "seed db restored: %d KB" % (total // 1024))
    except Exception as e:
        _log(root, "seed db FAILED: %r" % (e,))


def _start_llama(root: Path, lib_dir: str) -> None:
    """用 libllamaserver.so（其实就是 llama-server 可执行文件）起本地推理。"""
    binp = ""
    if lib_dir:
        cand = os.path.join(lib_dir, "libllamaserver.so")
        if os.path.exists(cand):
            binp = cand
    if not binp:
        import glob as _g
        hits = _g.glob("/data/app/*/*/lib/*/libllamaserver.so")
        if hits:
            binp = hits[0]
    if not binp:
        _log(root, "llama: binary not found"); return
    model = _ensure_model(root)
    if not model or not os.path.exists(model):
        _log(root, "llama: model missing"); return
    try:
        os.chmod(binp, 0o755)
    except Exception:
        pass
    cmd = [binp, "--model", model, "--host", "127.0.0.1", "--port", "11435",
           "--ctx-size", "2048", "--threads", "4"]
    _log(root, "llama: bin=%s size=%d model=%s" % (binp, os.path.getsize(binp), model))
    def _run():
        try:
            with open(root / "llama.out", "ab") as lf:
                rc = subprocess.run(cmd, stdout=lf, stderr=lf).returncode
            _log(root, "llama-server exited rc=%d" % rc)
        except Exception as e:
            print("llama-server failed:", e)
    threading.Thread(target=_run, daemon=True).start()
    import httpx as _x
    for _ in range(120):
        try:
            if _x.get("http://127.0.0.1:11435/health", timeout=1).status_code == 200:
                _log(root, "llama: ready")
                _start_ollama_shim()
                return
        except Exception:
            pass
        time.sleep(0.5)
    _log(root, "llama: not ready in 60s")


def _start_ollama_shim() -> None:
    """把 llm.py 用的 Ollama 接口翻译成 llama-server 的 OpenAI 接口。

    llm.py 只调 /api/tags（探活）和 /api/generate，这里做等价转发，
    业务代码零改动。
    """
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import httpx as _x

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, obj):
            b = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)

        def do_GET(self):
            if self.path.startswith("/api/tags"):
                self._send(200, {"models": [{"name": llm_model_name()}]})
            else:
                self._send(404, {})

        def do_POST(self):
            if not self.path.startswith("/api/generate"):
                self._send(404, {}); return
            n = int(self.headers.get("Content-Length") or 0)
            req = _json.loads(self.rfile.read(n) or b"{}")
            opts = req.get("options") or {}
            body = {
                "messages": [{"role": "user", "content": req.get("prompt", "")}],
                "temperature": opts.get("temperature", 0.2),
                "max_tokens": opts.get("num_predict", 400),
                "stream": False,
            }
            try:
                r = _x.post("http://127.0.0.1:11435/v1/chat/completions",
                            json=body, timeout=180)
                txt = r.json()["choices"][0]["message"]["content"]
            except Exception as e:
                print("shim error:", e)
                txt = ""
            self._send(200, {"response": txt, "done": True})

    def _serve():
        ThreadingHTTPServer(("127.0.0.1", 11434), H).serve_forever()

    threading.Thread(target=_serve, daemon=True).start()
    print("ollama shim: ready on 11434")


def llm_model_name() -> str:
    return "qwen2.5-0.5b"


def main(data_dir: str, lib_dir: str = "", port: int = 8765) -> None:
    root = Path(data_dir)
    root.mkdir(parents=True, exist_ok=True)
    os.chdir(root)
    cfg = root / "config.yaml"
    abs_data = str(root / "data").replace("\\", "/")
    text = DEFAULT_CFG.replace("{DATA_DIR}", abs_data)
    if not cfg.exists():
        cfg.write_text(text, encoding="utf-8")
    else:
        # 每次都把 data_dir 钉成绝对路径，否则会落到只读的 APK 解包目录
        lines = cfg.read_text(encoding="utf-8").splitlines()
        out = []
        seen = False
        for ln in lines:
            if ln.strip().startswith("data_dir:"):
                out.append("  data_dir: " + abs_data); seen = True
            else:
                out.append(ln)
        if not seen:
            out.insert(1, "  data_dir: " + abs_data)
        cfg.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.environ["UAVWATCH_CONFIG"] = str(cfg)
    here = Path(__file__).resolve().parent
    ui_dst = root / "mobile"
    if not (ui_dst / "index.html").exists():
        ui_dst.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(here / "mobile_ui" / "index.html", ui_dst / "index.html")
        except Exception:
            pass
    _ensure_seed_db(root)
    threading.Thread(target=_start_llama, args=(root, lib_dir), daemon=True).start()
    import server_mobile as sm
    sm.ROOT = root
    import uvicorn
    uvicorn.run(sm.app, host="127.0.0.1", port=port, log_level="warning")