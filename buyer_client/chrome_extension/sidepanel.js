// TopTenBuyer.ai - Chrome side panel assistant. Talks to the same backend as the app,
// no login (matches the app), and can read the current page to "spectate" what you're on.
let BACKEND = "https://topten-buyer.onrender.com";
const ALLDEPTS = ["THC", "Wine", "Spirits", "Beer", "Other"];
let history = [];
const $ = id => document.getElementById(id);
const money = n => "$" + Math.round(n || 0).toLocaleString();
function ago(iso){ if(!iso) return ''; const t=new Date(/[Z+]/.test(iso)?iso:iso+'Z'); const s=Math.max(0,(Date.now()-t.getTime())/1000); if(s<90) return Math.round(s)+' sec ago'; const m=s/60; if(m<90) return Math.round(m)+' min ago'; const h=m/60; if(h<36) return Math.round(h)+' hours ago'; return Math.round(h/24)+' days ago'; }
function fmtd(d){ if(!d) return ''; const p=String(d).split('-'); return p.length>=3?(+p[1])+'/'+(+p[2]):d; }

chrome.storage.local.get(["backend"], d => {
  if (d.backend) BACKEND = d.backend;
  $("beShow").textContent = BACKEND;
  start();
});

function setBackend() {
  const v = prompt("Backend URL:", BACKEND);
  if (v) { BACKEND = v.replace(/\/$/, ""); chrome.storage.local.set({ backend: BACKEND }); $("beShow").textContent = BACKEND; loadToday(); }
}

function start() {
  $("dept").innerHTML = ALLDEPTS.map(d => `<option>${d}</option>`).join("");
  loadToday();
}

async function loadToday() {
  history = []; $("msgs").innerHTML = "";
  try {
    const d = await (await fetch(BACKEND + "/api/today?dept=" + encodeURIComponent($("dept").value))).json();
    const h = d.headline || {}; const st = d.status || {};
    $("updated").textContent = st.built_utc ? ("Updated " + ago(st.built_utc) + (st.data_date ? (" · data " + fmtd(st.data_date)) : "")) : "";
    $("cards").innerHTML = h.items !== undefined ? `
      <div class="card"><div class="l">Net buy</div><div class="v">${money(h.net_buy)}</div></div>
      <div class="card big"><div class="l">Transfer</div><div class="v">${money(h.transfer)}</div></div>
      <div class="card"><div class="l">Items</div><div class="v">${h.items}</div></div>` : "";
  } catch (e) { $("cards").innerHTML = '<div class="muted">Backend unreachable.</div>'; }
}

function bubble(text, cls) {
  const m = document.createElement("div"); m.className = "msg " + cls; m.textContent = text;
  $("msgs").appendChild(m); $("msgs").scrollTop = 1e9; return m;
}

async function send(userText, displayText) {
  bubble(displayText, "me"); history.push({ role: "user", content: userText });
  const t = bubble("Thinking…", "ai");
  try {
    const r = await fetch(BACKEND + "/api/chat", { method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ dept: $("dept").value, messages: history }) });
    const d = await r.json(); t.textContent = d.reply; history.push({ role: "assistant", content: d.reply });
  } catch (e) { t.textContent = "(Error reaching the assistant)"; }
}

function ask() {
  const q = $("q").value.trim(); if (!q) return; $("q").value = "";
  send(q, q);
}

// Read the current tab's visible text so the assistant can "spectate" what you're looking at.
async function evaluatePage() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    const [{ result }] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => document.body.innerText.slice(0, 6000)
    });
    const typed = $("q").value.trim(); $("q").value = "";
    const question = typed || "I'm looking at this page while buying. Is anything here worth buying (and how much) or worth skipping, based on our data?";
    const prompt = question + "\n\n[The page I'm looking at — " + (tab.title || tab.url) + "]:\n" + result;
    send(prompt, (typed ? typed : "Read this page") + " — " + (tab.title || tab.url));
  } catch (e) {
    bubble("Couldn't read this page (some pages block it).", "ai");
  }
}

$("dept").addEventListener("change", loadToday);
$("evalBtn").addEventListener("click", evaluatePage);
$("sendBtn").addEventListener("click", ask);
$("q").addEventListener("keydown", e => { if (e.key === "Enter") ask(); });
$("editBackend").addEventListener("click", setBackend);
