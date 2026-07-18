"""
app.py
------
Local web app: paste a URL, watch it crawl with live animated progress,
then download the content as Word / Markdown / JSON.

Run:
    pip install -r requirements.txt
    python app.py
Then open http://127.0.0.1:5000
"""

import json
import os
import queue
import tempfile
import threading
import uuid
from urllib.parse import urlparse

from flask import (Flask, Response, jsonify, render_template,
                   request, send_file, abort)

from scraper import crawl
from builders import build_docx, build_markdown, build_json

app = Flask(__name__)

# in-memory job registry  { job_id: {queue, thread, files, stop, done} }
JOBS = {}


def _safe_name(url: str) -> str:
    host = urlparse(url).netloc.replace("www.", "") or "site"
    return "".join(c if c.isalnum() or c in "-." else "_" for c in host)


def run_job(job_id, url, max_pages, delay, same_domain, formats):
    job = JOBS[job_id]
    q = job["queue"]

    def on_event(payload):
        q.put(payload)

    try:
        pages = crawl(url, max_pages=max_pages, delay=delay,
                      same_domain=same_domain, on_event=on_event,
                      should_stop=lambda: job["stop"])

        if not pages:
            q.put({"type": "error", "msg": "No pages could be extracted."})
            q.put({"type": "__end__"})
            return

        tmp = tempfile.mkdtemp(prefix="sce_")
        base = _safe_name(url)
        files = {}

        if "docx" in formats:
            p = os.path.join(tmp, f"{base}-content.docx")
            build_docx(pages, p, url)
            files["docx"] = p
        if "md" in formats:
            p = os.path.join(tmp, f"{base}-content.md")
            build_markdown(pages, p, url)
            files["md"] = p
        if "json" in formats:
            p = os.path.join(tmp, f"{base}-content.json")
            build_json(pages, p, url)
            files["json"] = p

        job["files"] = files
        q.put({"type": "done",
               "pages": len(pages),
               "formats": list(files.keys())})
    except Exception as exc:  # noqa: BLE001  – surface any crawl error to UI
        q.put({"type": "error", "msg": str(exc)})
    finally:
        job["done"] = True
        q.put({"type": "__end__"})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/start", methods=["POST"])
def start():
    data = request.get_json(force=True)
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {"queue": queue.Queue(), "thread": None,
                    "files": {}, "stop": False, "done": False}

    t = threading.Thread(
        target=run_job,
        args=(job_id, url,
              int(data.get("max_pages", 100)),
              float(data.get("delay", 0.3)),
              bool(data.get("same_domain", True)),
              data.get("formats", ["docx", "md", "json"])),
        daemon=True,
    )
    JOBS[job_id]["thread"] = t
    t.start()
    return jsonify({"job_id": job_id})


@app.route("/stream/<job_id>")
def stream(job_id):
    job = JOBS.get(job_id)
    if not job:
        abort(404)

    def gen():
        q = job["queue"]
        while True:
            try:
                event = q.get(timeout=30)
            except queue.Empty:
                yield ": keep-alive\n\n"
                continue
            if event.get("type") == "__end__":
                break
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache",
                             "X-Accel-Buffering": "no"})


@app.route("/stop/<job_id>", methods=["POST"])
def stop(job_id):
    job = JOBS.get(job_id)
    if job:
        job["stop"] = True
    return jsonify({"ok": True})


@app.route("/download/<job_id>/<fmt>")
def download(job_id, fmt):
    job = JOBS.get(job_id)
    if not job or fmt not in job["files"]:
        abort(404)
    path = job["files"][fmt]
    return send_file(path, as_attachment=True,
                     download_name=os.path.basename(path))


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5005))
    host = os.environ.get("HOST", "0.0.0.0")
    print(f" * Site Content Extractor running on http://{host}:{port}")
    try:
        from waitress import serve
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(debug=False, threaded=True, host=host, port=port)
