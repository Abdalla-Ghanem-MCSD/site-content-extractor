// app.js – drives the UI, talks to the Flask backend over SSE.

const $ = (id) => document.getElementById(id);

let jobId = null;
let evtSource = null;

const FMT_LABELS = {
  docx: { name: "Word document", ext: ".docx" },
  md:   { name: "Markdown",      ext: ".md"   },
  json: { name: "JSON data",     ext: ".json" },
};

// ---- animated count-up for the stat numbers ----
function animateNumber(el, target) {
  const current = parseInt(el.textContent, 10) || 0;
  if (current === target) return;
  const step = Math.max(1, Math.ceil(Math.abs(target - current) / 12));
  const dir = target > current ? 1 : -1;
  let val = current;
  const tick = () => {
    val += dir * step;
    if ((dir === 1 && val >= target) || (dir === -1 && val <= target)) {
      val = target;
    }
    el.textContent = val;
    if (val !== target) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function selectedFormats() {
  return [...document.querySelectorAll(".chip input:checked")].map((c) => c.value);
}

function reset() {
  if (evtSource) { evtSource.close(); evtSource = null; }
  jobId = null;
  $("pageList").innerHTML = "";
  $("barFill").style.width = "0%";
  ["sCrawled", "sQueued", "sErrors"].forEach((id) => ($(id).textContent = "0"));
  $("downloads").innerHTML = "";
  $("progressCard").classList.add("hidden");
  $("doneCard").classList.add("hidden");
  $("inputCard").classList.remove("hidden");
  const b = $("startBtn");
  b.classList.remove("loading");
  b.disabled = false;
}

// ---- start crawl ----
async function start() {
  const url = $("url").value.trim();
  if (!url) { $("url").focus(); return; }

  const formats = selectedFormats();
  if (formats.length === 0) { alert("Pick at least one output format."); return; }

  const btn = $("startBtn");
  btn.classList.add("loading");
  btn.disabled = true;

  const body = {
    url,
    max_pages: parseInt($("maxPages").value, 10) || 100,
    delay: parseFloat($("delay").value) || 0,
    same_domain: $("sameDomain").checked,
    formats,
  };

  let res;
  try {
    res = await fetch((window.__BASE__||"") + "/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => r.json());
  } catch (e) {
    alert("Could not reach the server.");
    btn.classList.remove("loading"); btn.disabled = false;
    return;
  }

  if (res.error) {
    alert(res.error);
    btn.classList.remove("loading"); btn.disabled = false;
    return;
  }

  jobId = res.job_id;
  $("inputCard").classList.add("hidden");
  $("progressCard").classList.remove("hidden");
  $("statusText").textContent = "Crawling…";

  listen(jobId);
}

// ---- SSE stream ----
function listen(id) {
  evtSource = new EventSource((window.__BASE__||"") + `/stream/${id}`);

  evtSource.onmessage = (e) => {
    const data = JSON.parse(e.data);

    if (data.type === "page") {
      addPage(data);
    } else if (data.type === "progress") {
      animateNumber($("sCrawled"), data.crawled);
      animateNumber($("sQueued"), data.queued);
      animateNumber($("sErrors"), data.errors);
      const total = data.crawled + data.queued;
      const pct = total ? Math.round((data.crawled / total) * 100) : 0;
      $("barFill").style.width = pct + "%";
    } else if (data.type === "log") {
      $("statusText").textContent = data.msg;
    } else if (data.type === "done") {
      finish(data);
    } else if (data.type === "error") {
      $("statusText").textContent = "Error: " + data.msg;
      $("statusLine").querySelector(".pulse").style.background = "var(--err)";
    }
  };

  evtSource.onerror = () => {
    // stream closed by server when the job ends – that's normal
    if (evtSource) { evtSource.close(); evtSource = null; }
  };
}

function addPage(d) {
  const li = document.createElement("li");
  const main = document.createElement("div");
  main.className = "pl-main";
  const title = document.createElement("div");
  title.className = "pl-title";
  title.textContent = d.title;
  const u = document.createElement("div");
  u.className = "pl-url";
  u.textContent = d.url;
  main.appendChild(title);
  main.appendChild(u);

  const tag = document.createElement("span");
  tag.className = "pl-tag";
  tag.textContent = d.blocks + " blocks";

  li.appendChild(main);
  li.appendChild(tag);
  const list = $("pageList");
  list.insertBefore(li, list.firstChild);
}

function finish(data) {
  $("barFill").style.width = "100%";
  setTimeout(() => {
    $("progressCard").classList.add("hidden");
    $("doneCard").classList.remove("hidden");
    $("doneSub").textContent =
      `Extracted ${data.pages} page${data.pages === 1 ? "" : "s"}.`;

    const wrap = $("downloads");
    wrap.innerHTML = "";
    data.formats.forEach((fmt, i) => {
      const a = document.createElement("a");
      a.className = "dl";
      a.href = (window.__BASE__||"") + `/download/${jobId}/${fmt}`;
      a.style.animationDelay = `${i * 0.08}s`;
      const meta = FMT_LABELS[fmt] || { name: fmt, ext: "" };
      a.innerHTML =
        `<span>Download <strong>${meta.name}</strong> ` +
        `<span class="ext">${meta.ext}</span></span><span class="arrow">↓</span>`;
      wrap.appendChild(a);
    });
  }, 500);
}

// ---- stop ----
function stop() {
  if (jobId) {
    fetch((window.__BASE__||"") + `/stop/${jobId}`, { method: "POST" });
    $("statusText").textContent = "Stopping…";
  }
}

// ---- wiring ----
$("startBtn").addEventListener("click", start);
$("stopBtn").addEventListener("click", stop);
$("againBtn").addEventListener("click", reset);
$("url").addEventListener("keydown", (e) => { if (e.key === "Enter") start(); });
