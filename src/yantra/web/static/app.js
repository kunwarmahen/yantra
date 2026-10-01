/* yantra web UI — a dumb renderer over the envelope protocol.
   One websocket carries everything; REST handles plain controls.
   On every (re)connect the transcript is rebuilt from the server's
   history replay, so reloads and reconnects are always consistent. */

"use strict";

const $ = (sel) => document.querySelector(sel);
const transcript = $("#transcript");

/* Every icon in the UI is a <use> of the sprite sheet in index.html --
   one stroke weight, one grid, currentColor throughout. */
function icon(name, cls = "ico", stroke = 1.8) {
  return `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor"`
    + ` stroke-width="${stroke}" stroke-linecap="round" stroke-linejoin="round"`
    + ` aria-hidden="true"><use href="#i-${name}"/></svg>`;
}

const ui = {
  ws: null,
  connected: false,
  turnActive: false,
  staged: [],            // [{filename, data_base64, url}]
  currentAssistant: null, // streaming text node
  assistantText: "",      // raw markdown accumulated for it
  renderQueued: false,    // rAF throttle for live re-rendering
  currentThinking: null,
  openToolCard: null,     // tool_start awaiting its result
  modalId: null,
  lastState: null,        // most recent state envelope (for mid-turn merges)
};

function forgetAssistant() {
  ui.currentAssistant = null;
  ui.assistantText = "";
}

/* ---------- connection ---------- */

function connect() {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const ws = new WebSocket(`${proto}//${location.host}/ws`);
  ui.ws = ws;

  ws.onopen = () => { setOffline(false); };
  ws.onclose = () => {
    setOffline(true);
    setTimeout(connect, 1500);
  };
  ws.onerror = () => ws.close();

  ws.onmessage = (m) => {
    let env;
    try { env = JSON.parse(m.data); } catch { return; }
    route(env);
  };
}

function send(obj) {
  if (ui.ws && ui.ws.readyState === WebSocket.OPEN) {
    ui.ws.send(JSON.stringify(obj));
  }
}

function setOffline(off) {
  $("#offline").classList.toggle("hidden", !off);
  if (off) { ui.connected = false; }
}

/* ---------- envelope routing ---------- */

function route(env) {
  switch (env.type) {
    case "state":          onState(env); break;
    case "turn_started":   beginTurn(); break;
    case "start":          setStatus(`· ${env.model}`); break;

    case "delta":              appendDelta(env.text); break;
    case "thinking_delta":     appendThinking(env.text); break;
    case "redacted_thinking":  addRedacted(env.chars); break;

    case "tool_start":         openToolCard(env.name); break;
    case "tool_result":        fillToolCard(env);
                               // live envelopes carry fresh pressure; replay
                               // ones don't -- only merge when present
                               if (env.utilization != null && ui.lastState) {
                                 renderPressure(
                                   {...ui.lastState, utilization: env.utilization});
                               }
                               mergeBudget(env);
                               break;

    case "permission_request": showPermissionModal(env); break;
    case "ask":                showAskModal(env); break;
    case "learn_offer":        showLearnModal(env); break;
    case "tool_offer":         showToolModal(env); break;
    case "setu_signin":        onSignin(env); break;
    case "connections":        onConnections(env); break;
    case "memory_notice":      addBanner(env.text, false, true); break;
    case "memory_offer":       offerMemories(env.candidates); break;
    case "promoted":           toast(`${env.skill} is now the tool ${env.tool}`);
                               addBanner(`${env.skill} is now the tool `
                                 + `${env.tool} — next time it is one call `
                                 + `(each call still asks first, like bash)`, false);
                               break;
    case "learn_status":       setStatus(`· ${env.text}`); break;
    case "learn_counted":      /* the skills panel shows the new counts */ break;
    case "learned":            toast(`saved skill ${env.name}`);
                               addBanner(`saved skill ${env.name} — next time `
                                 + `the model follows it instead of finding `
                                 + `the way again (${env.path})`, false);
                               break;
    case "learn_skipped":      if (env.reason) addBanner(env.reason, false, true);
                               break;
    case "resolved":           closeModalIf(env.id); break;

    case "budget_warning": onBudgetWarning(env); break;
    case "turn_end":       onTurnEnd(env); break;
    case "turn_error":     addBanner(env.message, true); break;
    case "turn_cancelled": addBanner("turn cancelled", false, true); break;
    case "recorded":       addMarkRow(env.id); refreshTurnsPanel(); break;
    case "turn_done":      endTurn(); break;

    /* history replay shapes */
    case "user_message":    addUserMessage(env.text, env.images, true); break;
    case "assistant_text":  assistantTextDone(env.text); break;
    case "thinking_done":   addThinkingDone(env.text); break;
  }
}

function onState(env) {
  // First envelope of a connection: rebuild the transcript from scratch so
  // reconnects never duplicate what we already rendered live.
  if (!ui.connected) {
    ui.connected = true;
    transcript.textContent = "";
    forgetAssistant();
    ui.currentThinking = ui.openToolCard = null;
    fetchHistory();
  }
  applyHeader(env);
  if (!conn.loaded) loadConnections();
  // A turn held before this page was opened (or reloaded) still waits.
  // On a first connect the history is still being replayed, and the
  // panel belongs AFTER it -- fetchHistory draws it once that is done.
  if (ui.historyLoaded && env.held && !ui.turnActive && !$("#held-panel")) {
    renderHeld(env.held);
  }
}

/* A turn stopped for approval (--on-timeout hold, notes/88). Unlike the
   modal, this stays in the transcript: nobody is waiting on a clock now,
   so the questions sit where the turn stopped until the person answers
   them all -- or sends a new message, which sets them aside. */
function renderHeld(held) {
  $("#held-panel")?.remove();
  const ago = held.age < 90 ? `${held.age}s` : `${Math.round(held.age / 60)} min`;
  const rows = held.calls.map((c) => `
    <div class="held-call" data-id="${esc(c.id)}">
      <div class="held-head"><b>${esc(c.tool_name)}()</b>
        <label><input type="radio" name="h-${esc(c.id)}" value="approve" checked> approve</label>
        <label><input type="radio" name="h-${esc(c.id)}" value="deny"> deny</label>
      </div>
      <div class="summary">${esc(c.summary)}</div>
      <details><summary>raw arguments</summary>
        <pre class="args">${esc(JSON.stringify(c.arguments ?? {}, null, 2))}</pre>
      </details>
      <input class="deny-reason" type="text"
             placeholder="optional: say why not, or what to do instead">
    </div>`).join("");
  const el = document.createElement("div");
  el.id = "held-panel";
  el.className = "held-panel";
  el.innerHTML = `
    <div class="kind-tag">waiting for you</div>
    <div class="held-age">This turn stopped ${ago} ago. What you approve runs
      against things as they are now, not as they were then.</div>
    ${rows}
    <div class="held-actions">
      <span class="held-note">or send a new message to set these aside</span>
      <button class="m-btn primary" id="held-send">carry on</button>
    </div>`;
  // A reason typed under a call means no, whichever button is ticked.
  el.querySelectorAll(".deny-reason").forEach((box) => {
    box.oninput = () => {
      const deny = box.closest(".held-call").querySelector('input[value="deny"]');
      if (box.value.trim()) deny.checked = true;
    };
  });
  el.querySelector("#held-send").onclick = async () => {
    const answers = {};
    el.querySelectorAll(".held-call").forEach((row) => {
      const decision = row.querySelector("input[type=radio]:checked").value;
      answers[row.dataset.id] = { decision,
        reason: row.querySelector(".deny-reason").value };
    });
    el.querySelector("#held-send").disabled = true;
    if (await post("/api/resume", { answers })) el.remove();
    else el.querySelector("#held-send").disabled = false;
  };
  transcript.append(el);
  scrollDown();
}

async function fetchHistory() {
  try {
    const res = await fetch("/api/history");
    if (!res.ok) return;
    for (const env of await res.json()) route(env);
    ui.historyLoaded = true;
    const held = ui.lastState?.held;
    if (held && !ui.turnActive) renderHeld(held);   // where the turn stopped
    scrollDown(true);
  } catch { /* offline; retry happens via reconnect */ }
}

function applyHeader(s) {
  ui.lastState = s;
  $("#provider-name").textContent = s.provider;
  $("#model-name").textContent = s.model;
  const yolo = s.mode === "yolo";
  $("#mode-label").textContent = s.mode || "ask";
  $("#chip-mode").classList.toggle("chip-yolo", yolo);
  // session awareness chip: label is the level, tooltip carries the exact
  // fact sheet the model sees (null when the host built no EnvContext)
  const ec = s.env_context;
  $("#env-label").textContent = ec ? ec.mode : "—";
  $("#chip-env").classList.toggle("chip-env-full", !!ec && ec.mode === "full");
  $("#chip-env").title = (ec && ec.block)
    ? `session awareness — click to cycle off ⇄ local ⇄ full\n\n${ec.block}`
    : "session awareness — click cycles off ⇄ local ⇄ full";
  $("#chip-cost").textContent = s.cost_line || "";
  $("#chip-cost").title = "session cost — unknown slugs show no figure, "
    + "never a guess" + (s.budget ? `\n\nbudget: ${s.budget}` : "");
  // tools chip: "N" live, "+M off" when the operator pulled some
  const off = (s.disabled_tools || []).length;
  $("#tools-count").textContent =
    (s.tools ? s.tools.length : "—") + (off ? ` · ${off} off` : "");
  // memory chip: hidden when memory is off; the count is what this
  // conversation's prompt carries, the tooltip says whose and where
  const mem = s.memory;
  $("#chip-mem").classList.toggle("hidden", !mem);
  if (mem) {
    $("#mem-count").textContent = `${mem.in_prompt} in prompt`;
    $("#chip-mem").title = `memory — ${mem.store} store, for ${mem.user}`
      + (mem.notice ? `\n\n${mem.notice}` : "");
  }
  renderPressure(s);
  renderBudget(s);
  renderRecording(s);
  setTurnUI(s.turn_active);
}

/* Mid-turn envelopes carry a fresh meter; replayed ones do not. Merging
   into lastState rather than re-rendering from the envelope alone keeps
   one source of truth for the header, so a reconnect cannot leave the
   bar showing a number from the turn before. */
function mergeBudget(env) {
  if (env.budget_meter == null || !ui.lastState) return;
  ui.lastState = {...ui.lastState, budget_meter: env.budget_meter};
  renderBudget(ui.lastState);
}

/* What is LEFT of this turn's dollar ceiling, drawn on the same bar as
   context pressure and with the same thresholds, because they are the
   same kind of fact: how much of something finite this turn has used.

   Three states, and the third is the one worth getting right. No ceiling
   at all: no chip. A ceiling that CANNOT fire -- a local model, which
   bills nothing -- draws an empty bar and says "free", because a full
   bar that will never move implies a protection nobody has. A live
   ceiling fills, and the number beside it is what remains rather than
   what is spent: "$0.07 left" is the figure somebody acts on. */
function renderBudget(s) {
  const meter = s.budget_meter;
  $("#chip-budget").classList.toggle("hidden", meter == null);
  if (meter == null) return;
  const told = meter.tells_agent ? "; the agent is told when it is close" : "";
  if (!meter.metered) {
    $("#budget-fill").style.width = "0%";
    $("#budget-fill").className = "ctxbar-fill";
    $("#budget-left").textContent = "free";
    $("#chip-budget").classList.remove("chip-ctx-danger");
    $("#chip-budget").title =
      `per-turn ceiling $${meter.max_usd.toFixed(2)} — inert here, a local ` +
      `model bills nothing${told}`;
    return;
  }
  const pct = meter.max_usd ? (meter.spent / meter.max_usd) * 100 : 0;
  const left = Math.max(meter.max_usd - meter.spent, 0);
  $("#budget-fill").style.width = `${Math.min(pct, 100)}%`;
  $("#budget-fill").className = "ctxbar-fill"
    + (pct >= 80 ? " danger" : pct >= 60 ? " warn" : "");
  $("#budget-left").textContent = `$${left.toFixed(left < 0.01 ? 4 : 2)} left`;
  $("#chip-budget").classList.toggle("chip-ctx-danger", pct >= 80);
  // The sub-agents' share: already inside ``spent``, said separately
  // because one bar cannot show that most of a turn went to a child.
  const delegated = meter.delegated
    ? `, ~$${meter.delegated.toFixed(4)} of it by sub-agents` : "";
  $("#chip-budget").title =
    `this turn has spent ~$${meter.spent.toFixed(4)} of its ` +
    `$${meter.max_usd.toFixed(2)} ceiling${delegated}; the turn stops ` +
    `between iterations once it is crossed${told}`;
}

/* The recording chip: visible only while --trace is writing turns down.
   "shape" keeps the task, the tools and the counts; "full" keeps what the
   agent read as well, which is the one worth noticing from across a room. */
function renderRecording(s) {
  const rec = s.recording;
  $("#chip-rec").classList.toggle("hidden", !rec);
  if (!rec) return;
  $("#rec-label").textContent = rec.detail;
  $("#chip-rec").classList.toggle("chip-ctx-danger", rec.detail === "full");
  $("#chip-rec").title = `every turn is being recorded to ${rec.path} (` +
    (rec.detail === "full"
      ? "FULL: tool arguments, results and answers -- whatever the agent read"
      : "shape: the task, which tools ran and the counts; no contents") + ")"
    + (rec.redacting || rec.redacting_words || rec.reading_names
      ? "; " + [rec.redacting ? `${rec.redacting} redaction pattern(s)` : "",
                rec.redacting_words ? `${rec.redacting_words} listed word(s)` : "",
                rec.reading_names ? `names found by ${rec.reading_names}` : ""]
          .filter(Boolean).join(" and ") + " scrubbed before anything is written"
      : "")
    + " — click for the turns recorded so far";
}

/* Context-window pressure. The bar mirrors auto-compaction's thresholds
   (60% yellow / 80% red); under 10% we show a decimal so early-session
   readings aren't all rounded to a lying flat 0%. The tooltip carries
   the honest numbers behind it (real provider count once one exists,
   chars/4 estimate before that). */
function renderPressure(s) {
  const util = s.utilization;
  $("#chip-ctxbar").classList.toggle("hidden", util == null);
  if (util == null) return;
  const pct = util * 100;
  const shown = pct < 10 ? pct.toFixed(1) : String(Math.round(pct));
  $("#ctx-fill").style.width = `${Math.min(pct, 100)}%`;
  $("#ctx-fill").className = "ctxbar-fill"
    + (pct >= 80 ? " danger" : pct >= 60 ? " warn" : "");
  $("#ctx-pct").textContent = `${shown}%`;
  $("#chip-ctxbar").classList.toggle("chip-ctx-danger", pct >= 80);
  $("#chip-ctxbar").title =
    `context pressure — ~${fmtNum(s.context_tokens)} of ` +
    `${fmtNum(s.context_window)} window tokens` +
    (s.context_estimated ? " (estimated)" : "") +
    "; auto-compact at 80%";
}

function fmtNum(n) {
  return n >= 10_000 ? `${(n / 1000).toFixed(n % 1000 ? 1 : 0)}k`
    : String(n ?? 0);
}

/* ---------- turn lifecycle ---------- */

function beginTurn() {
  ui.turnActive = true;
  // Answered, or set aside by a new message: either way, no longer waiting.
  $("#held-panel")?.remove();
  forgetAssistant();
  ui.currentThinking = ui.openToolCard = null;
  setStatus("connecting…");
  setTurnUI(true);
}

function endTurn() {
  ui.turnActive = false;
  forgetAssistant();
  ui.currentThinking = ui.openToolCard = null;
  hideStatus();
  setTurnUI(false);
}

function setTurnUI(active) {
  ui.turnActive = active;
  $("#btn-send").disabled = active;
  $("#btn-cancel").classList.toggle("hidden", !active);
  if (!active) hideStatus();
}

function setStatus(text) {
  $("#statusline").classList.remove("hidden");
  $("#status-text").textContent = text;
}
function hideStatus() { $("#statusline").classList.add("hidden"); }

// Following the bottom is decided by where YOU last left the scrollbar,
// never measured after an append: one tall tool panel lands far more
// than any threshold below the fold and would read as "scrolled up".
let followBottom = true;
transcript.addEventListener("scroll", () => {
  followBottom =
    transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 80;
}, { passive: true });

function scrollDown(force) {
  if (force) followBottom = true;
  if (followBottom) transcript.scrollTop = transcript.scrollHeight;
}

/* ---------- message rendering ---------- */

function addUserMessage(text, images = 0, replay = false) {
  const wrap = document.createElement("div");
  wrap.className = "msg-user";
  const label = document.createElement("div");
  label.className = "label";
  label.textContent = "you";
  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = text;
  wrap.append(label, bubble);
  if (images) {
    const note = document.createElement("div");
    note.className = "imgs";
    note.innerHTML = icon("image", "ico-sm")
      + `<span>${images} image${images > 1 ? "s" : ""} attached</span>`;
    wrap.append(note);
  }
  transcript.append(wrap);
  if (!replay) scrollDown(true);   // you just spoke: follow the reply
  return bubble;
}

function ensureAssistant() {
  if (!ui.currentAssistant) {
    const wrap = document.createElement("div");
    wrap.className = "msg-assistant";
    const label = document.createElement("div");
    label.className = "label";
    label.textContent = "agent";
    const textEl = document.createElement("div");
    textEl.className = "text empty";
    wrap.append(label, textEl);
    transcript.append(wrap);
    ui.currentAssistant = textEl;
  }
  return ui.currentAssistant;
}

function appendDelta(text) {
  ui.assistantText += text;
  const el = ensureAssistant();
  el.classList.remove("empty");
  // Live markdown re-render, throttled to one per animation frame:
  // a partial document (half a table, an open fence) renders as far as
  // it goes, and turn_end's full-text pass settles any artifact.
  if (!ui.renderQueued) {
    ui.renderQueued = true;
    requestAnimationFrame(() => {
      ui.renderQueued = false;
      if (ui.currentAssistant) {
        ui.currentAssistant.innerHTML = renderMarkdown(ui.assistantText);
        scrollDown();
      }
    });
  }
}

function assistantTextDone(text) {
  const el = ensureAssistant();
  el.classList.remove("empty");
  ui.assistantText = text; // replay replaces rather than appends
  el.innerHTML = renderMarkdown(text);
  scrollDown();
}

function ensureThinking() {
  if (!ui.currentThinking) {
    const el = document.createElement("div");
    el.className = "thinking";
    transcript.append(el);
    ui.currentThinking = el;
  }
  return ui.currentThinking;
}
function appendThinking(text) { ensureThinking().textContent += text; scrollDown(); }
function addThinkingDone(text) { ensureThinking().textContent = text; }

function addRedacted(chars) {
  const el = document.createElement("div");
  el.className = "redacted";
  el.textContent = `· redacted reasoning (${chars} chars, encrypted)`;
  transcript.append(el);
  scrollDown();
}

/* ---------- tool cards ---------- */

const OUTPUT_PREVIEW = 400;

function openToolCard(name) {
  ui.currentThinking = null; // thinking block ends where tools begin
  forgetAssistant();
  const card = document.createElement("div");
  card.className = "tool-card running";
  card.innerHTML = `<div class="tool-head">`
    + `<span class="tool-glyph">${icon("terminal", "ico-sm", 1.7)}</span>`
    + `<span class="tool-name">${esc(name)}()</span>`
    + `<span class="tool-status">running…</span></div>`;
  transcript.append(card);
  ui.openToolCard = card;
  scrollDown();
}

function fillToolCard(env) {
  let card = ui.openToolCard;
  // Replay has no tool_start before results — create as needed.
  if (!card) {
    openToolCard(env.name);
    card = ui.openToolCard;
  }
  card.classList.remove("running");
  // A refusal is not a crash: the gate turned this call away, and the
  // code says which kind — you said no, nobody answered, a policy did it.
  // Drawing both as "error" tells somebody their tool broke.
  const refusal = env.refusal ?? null;
  card.classList.toggle("error", !!env.is_error && !refusal);
  card.classList.toggle("refused", !!refusal);
  card.querySelector(".tool-status").textContent =
    refusal ? `refused · ${refusal}` : (env.is_error ? "error" : "ok");
  card.querySelector(".tool-name").textContent = `${env.name}()`;

  const body = document.createElement("div");
  body.className = "tool-body";

  const argsDetails = document.createElement("details");
  argsDetails.innerHTML = "<summary>arguments</summary>";
  const argsPre = document.createElement("pre");
  argsPre.className = "args";
  argsPre.textContent = JSON.stringify(env.arguments ?? {}, null, 2);
  argsDetails.append(argsPre);
  body.append(argsDetails);

  const outPre = document.createElement("pre");
  outPre.className = "output";
  const output = String(env.output ?? "");
  if (output.length > OUTPUT_PREVIEW && !env.is_error) {
    outPre.textContent = output.slice(0, OUTPUT_PREVIEW);
    const more = document.createElement("span");
    more.className = "output-clipped";
    more.textContent =
      `\n[… ${output.length - OUTPUT_PREVIEW} more chars — click to expand]`;
    more.onclick = () => { outPre.textContent = output; more.remove(); };
    body.append(outPre, more);
  } else {
    outPre.textContent = output;
    body.append(outPre);
  }
  card.append(body);
  ui.openToolCard = null;
  scrollDown();
}

/* ---------- banners / footer ---------- */

function addBanner(message, isError, cancelledStyle = false) {
  const el = document.createElement("div");
  el.className = isError ? "banner banner-error"
    : cancelledStyle ? "banner-cancelled" : "banner";
  el.textContent = message;
  transcript.append(el);
  scrollDown();
}

function onBudgetWarning(env) {
  mergeBudget(env);
  // Mid-turn advice, not an ending: the turn goes on around it, so it
  // renders as its own banner and the transcript keeps flowing.
  const el = document.createElement("div");
  el.className = "banner banner-warn";
  el.textContent = `budget: ${env.detail}`;
  transcript.append(el);
  scrollDown();
}

function onTurnEnd(env) {
  mergeBudget(env);   // the last charge of the turn lands here
  if (env.reason === "held" && env.held) {
    // Not an ending: the turn is waiting for you (notes/88).
    renderHeld(env.held);
    return;
  }
  if (env.reason !== "end_turn") {
    // A reply cut off by --budget-cap-reply carries its text: settle what
    // streamed before saying why it stops there (notes/76).
    if (env.text && env.text.trim()) assistantTextDone(env.text);
    const why = env.detail ? ` — ${env.detail}` : "";
    addBanner(`── turn ended: ${env.reason}${why} ` +
              `(after ${env.iterations} iteration(s))`, false);
    const tally = refusalTally(env.refused);
    if (tally) transcript.append(tally);
    return;
  }
  if (env.text && env.text.trim()) {
    assistantTextDone(env.text); // final full-text pass: settles streaming
  } else {
    // end_turn with only thinking/tool noise -- say so rather than
    // leaving the operator staring at silence (mirrors render.py)
    const el = ensureAssistant();
    el.classList.remove("empty");
    if (!ui.assistantText.trim()) el.textContent = env.stop_reason === "max_tokens"
      // Mirrors render.empty_reply_reason: silence after max_tokens is a
      // full context window, not a long answer cut short.
      ? "(no text in reply: the model ran out of room — the prompt likely " +
        "filled its context window; check <PROVIDER>_CONTEXT_WINDOW " +
        "matches the server's)"
      : "(no text in reply)";
  }
  const foot = document.createElement("div");
  foot.className = "turn-foot";
  const cost = env.cost_line ? ` · ~cost: ${env.cost_line}` : "";
  foot.textContent =
    `── ${env.stop_reason} · ${env.input_tokens} in / ${env.output_tokens} out` +
    `${cost} · ${env.iterations} iteration(s)`;
  const wrap = ensureAssistant().parentElement;
  wrap.append(foot);
  const tally = refusalTally(env.refused);
  if (tally) wrap.append(tally);
  ui.currentAssistant = null;
  scrollDown();
}

/* The terminal's per-turn tally (notes/52): refusals grouped by cause, or
   nothing. The cards above say WHICH calls were refused; this answers the
   end-of-turn question -- was anything refused for a reason that was not
   me? Counted by the server, so a tab that joined late still adds up. */
function refusalTally(refused) {
  const codes = Object.entries(refused || {});
  if (!codes.length) return null;
  const total = codes.reduce((n, [, k]) => n + k, 0);
  const el = document.createElement("div");
  el.className = "turn-foot turn-refused";
  el.textContent = `── ${total} call(s) refused: ` +
    codes.map(([code, n]) => `${code} ${n}`).join(" · ");
  return el;
}

/* A person's verdict on the turn just recorded (notes/77): the browser's
   --mark. Yantra never judges a turn; this row only writes down what the
   reader decided, into the same line the terminal command would. Offered
   only for turns recorded while this page watched -- a replayed
   transcript has no trace ids to write against. */
function addMarkRow(id) {
  transcript.append(markRow(id, {}));
  scrollDown();
}

/* The good/bad/take-it-back row for one recorded turn, painted from the
   mark it already has. Shared by the end-of-turn row and the turns panel,
   so a button writes the same line from either. */
function markRow(id, current, onMarked = null) {
  const row = document.createElement("div");
  row.className = "turn-mark";
  const paint = (mark) => {
    const verdict = mark.judged_by === "person"
      ? (mark.passed ? "good" : "bad") : null;
    row.innerHTML = `<span class="turn-mark-q">${verdict
        ? `you marked this <b>${verdict}</b>${mark.why
          ? ` — ${esc(mark.why)}` : ""}`
        : "was this turn right?"}</span>`
      + (verdict
        ? `<button class="mark-btn" data-v="clear">take it back</button>`
        : `<button class="mark-btn" data-v="good">${icon("check", "ico-sm")}good</button>`
          + `<button class="mark-btn" data-v="bad">${icon("x", "ico-sm")}bad</button>`)
      + `<span class="turn-mark-id" title="the id --turns and --fossil know it by">${esc(id.slice(0, 8))}</span>`;
  };
  row.onclick = async (e) => {
    const verdict = e.target.closest("button")?.dataset?.v;
    if (!verdict) return;
    let why = null;
    if (verdict === "bad") {
      why = await openDialog({
        title: "what was wrong?",
        body: "optional — your words become the description of the "
          + "regression case if this turn is ever turned into one.",
        placeholder: "e.g. cited a file it never opened",
        confirm: "mark bad",
      });
      if (why === null) return;     // backed out: nothing is written
    }
    const mark = await post("/api/mark", { id, verdict, why });
    if (mark) {
      paint(mark);
      if (onMarked) onMarked(mark);
    }
  };
  paint(current);
  return row;
}

/* ---------- recorded turns (notes/81) ---------- */

/* --turns inside the page. The end-of-turn row only exists for turns this
   page watched; a reload, another tab or yesterday's session leaves turns
   it never saw. This lists the file itself, newest first, flagged by the
   same rule the terminal uses (the server applies it), and each row can
   be marked like the one under a turn. */
$("#chip-rec").onclick = openTurnsPanel;
$("#turns-close").onclick = closeTurnsPanel;
$("#turns-backdrop").addEventListener("click", (e) => {
  if (e.target === $("#turns-backdrop")) closeTurnsPanel();
});
$("#turns-flagged").onchange = () => renderTurns(ui.turnsData);

async function openTurnsPanel() {
  $("#turns-backdrop").classList.remove("hidden");
  $("#turns-rows").innerHTML = '<div class="panel-loading">loading…</div>';
  await loadTurns();
}

async function loadTurns() {
  let data = null;
  try {
    const res = await fetch("/api/turns");
    data = await res.json().catch(() => ({}));
    if (!res.ok) { toast(data.detail || res.statusText); data = null; }
  } catch { /* stays null */ }
  ui.turnsData = data;
  renderTurns(data);
}

function renderTurns(data) {
  const rows = $("#turns-rows");
  if (!data) {
    rows.innerHTML = '<div class="panel-loading">could not read the recording</div>';
    return;
  }
  const shown = data.turns.length < data.total
    ? `the newest ${data.turns.length} of ${data.total}` : `${data.total}`;
  $("#turns-note").textContent = `${shown} turn(s) in ${data.path}, `
    + `${data.flagged} flagged` + (data.unreadable
      ? `, ${data.unreadable} unreadable line(s) skipped` : "")
    + " — an unflagged turn can still be wrong"
    + (data.turns.some((t) => t.answer == null && !t.withheld)
      ? ". Answers are kept only by --trace-full" : "");
  const onlyFlagged = $("#turns-flagged").checked;
  const turns = data.turns.filter((t) => !onlyFlagged || t.flagged);
  rows.innerHTML = "";
  if (!turns.length) {
    rows.innerHTML = `<div class="panel-loading">${data.total
      ? "nothing flagged" : "nothing recorded yet"}</div>`;
    return;
  }
  for (const t of turns) rows.append(turnRow(t));
}

function turnRow(t) {
  const row = document.createElement("div");
  const good = t.judged_by === "person" && t.passed === true;
  row.className = "turn-row" + (t.flagged ? " is-flagged" : "")
    + (good ? " is-good" : "");
  row.innerHTML =
    `<div class="turn-row-head"><span class="turn-row-flag">${
      t.flagged ? "✗" : good ? "✓" : ""}</span>`
    + `<span class="turn-row-id" title="the id --turns and --fossil know it by">${esc(t.id.slice(0, 8))}</span>`
    + `<span>${esc(t.at)}</span><span>${t.tools} tool(s)</span>`
    + (t.case ? `<span>case ${esc(t.case)}</span>` : "") + `</div>`
    + `<div class="turn-row-task">${esc(t.task)}</div>`
    + turnAnswer(t)
    + (t.flag_why ? `<div class="turn-row-why">${esc(t.flag_why)}</div>` : "");
  const answer = row.querySelector(".turn-row-answer");
  if (answer) answer.onclick = () => answer.classList.toggle("is-folded");
  // A mark changes whether the turn is flagged, and the server owns that
  // rule, so the list is read again rather than guessed at here.
  row.append(markRow(t.id, t, () => loadTurns()));
  return row;
}

/* What the agent said, so a mark is a judgement of the answer and not a
   guess from the task. Only a --trace-full line kept it; the header note
   says so once rather than every shape-level row saying "not kept". Long
   answers start folded to a few lines and open on a click. */
function turnAnswer(t) {
  if (t.withheld) {
    return `<div class="turn-row-why">contents withheld: ${esc(t.withheld)}</div>`;
  }
  if (t.answer == null) {
    return t.outcome && t.outcome !== "end_turn"
      ? `<div class="turn-row-why">stopped: ${esc(t.outcome)}</div>` : "";
  }
  return `<div class="turn-row-answer is-folded" title="click to unfold">${
    esc(t.answer)}</div>`;
}

function refreshTurnsPanel() {
  if (!$("#turns-backdrop").classList.contains("hidden")) loadTurns();
}

function closeTurnsPanel() {
  $("#turns-backdrop").classList.add("hidden");
}

/* ---------- modals ---------- */

function showModal(html) {
  $("#modal").innerHTML = html;
  $("#modal-backdrop").classList.remove("hidden");
}

function closeModalIf(id) {
  if (ui.modalId === id) {
    ui.modalId = null;
    clearInterval(ui.waitTimer);
    $("#modal-backdrop").classList.add("hidden");
  }
}

function showPermissionModal(env) {
  ui.modalId = env.id;
  showModal(`
    <div class="kind-tag">approval needed</div>
    <h3>${esc(env.tool_name)}() ${env.edited
      ? '<span class="edited-flag">(edited)</span>' : ""}</h3>
    ${env.wait_left != null ? '<div class="wait-clock" id="wait-clock"></div>' : ""}
    <div class="summary">${esc(env.summary)}</div>
    <details><summary>raw arguments</summary>
      <pre class="args">${esc(JSON.stringify(env.arguments ?? {}, null, 2))}</pre>
    </details>
    <input id="deny-reason" class="deny-reason" type="text"
           placeholder="optional: say why not, or what to do instead">
    <div class="modal-actions">
      <button class="m-btn" data-act="edit">edit</button>
      <button class="m-btn danger" data-act="deny">deny</button>
      <button class="m-btn primary" data-act="approve">approve ⏎</button>
    </div>`);
  startWaitClock(env);
  const answer = (payload) => send({ type: "answer", id: env.id, ...payload });
  const onKey = (e) => {
    // Enter approves only while THIS modal is up and no edit box is open
    if (ui.modalId !== env.id || e.key !== "Enter") return;
    if ($("#modal textarea")) return;
    // Enter inside the reason box denies WITH that sentence rather than
    // approving: a person who has just typed "no, use staging" and hit
    // Enter did not mean yes.
    if (document.activeElement === $("#deny-reason")) {
      e.preventDefault();
      document.removeEventListener("keydown", onKey);
      const said = $("#deny-reason").value;
      if (said.trim()) { answer({ decision: "deny", reason: said }); return; }
    }
    e.preventDefault();
    answer({ decision: "approve" });
  };
  document.addEventListener("keydown", onKey);
  $("#modal").onclick = (e) => {
    const act = e.target?.dataset?.act;
    if (!act) return;
    if (act === "approve") { document.removeEventListener("keydown", onKey); answer({ decision: "approve" }); }
    if (act === "deny") {
      document.removeEventListener("keydown", onKey);
      // Whatever the person typed rides along with the no: the model
      // reads it instead of "Permission denied by user." An empty box is
      // a plain no, not a refusal that says nothing.
      answer({ decision: "deny", reason: $("#deny-reason")?.value ?? "" });
    }
    if (act === "edit") renderEditBox(env, answer, onKey);
  };
}

/* This turn's approval allowance (--wait-budget, notes/78), counting down.
   The server keeps the real clock and withdraws the prompt when it runs
   out; this is only so the person can see it coming. */
function startWaitClock(env) {
  clearInterval(ui.waitTimer);
  if (env.wait_left == null) return;
  // Once per prompt: "back" from the edit box redraws this modal, and
  // must not hand the person their seconds back.
  env.waitUntil ??= performance.now() + env.wait_left * 1000;
  const until = env.waitUntil;
  const silence = {allow: "it goes ahead", hold: "the turn waits for you"}[
    ui.lastState?.wait_budget?.on_timeout] ?? "it is refused";
  const tick = () => {
    const el = $("#wait-clock");
    if (!el) { clearInterval(ui.waitTimer); return; }
    const left = Math.max(0, (until - performance.now()) / 1000);
    el.textContent = `${left.toFixed(0)}s of this turn's waiting left — `
      + `unanswered, ${silence}`;
    el.classList.toggle("wait-clock-low", left < 10);
  };
  tick();
  ui.waitTimer = setInterval(tick, 500);
}

function renderEditBox(env, answer, dismissKeyHandler) {
  const box = document.createElement("div");
  box.innerHTML = `
    <textarea id="edit-args">${esc(JSON.stringify(env.arguments ?? {}, null, 2))}</textarea>
    <div class="modal-actions">
      <button class="m-btn" id="edit-cancel">back</button>
      <button class="m-btn primary" id="edit-review">review edited call</button>
    </div>`;
  [...$("#modal").children].forEach((c) => {
    if (!c.classList.contains("kind-tag")) c.remove();
  });
  $("#modal").append(box);
  box.querySelector("#edit-cancel").onclick = () => showPermissionModal(env);
  box.querySelector("#edit-review").onclick = () => {
    try {
      const edited = JSON.parse(box.querySelector("#edit-args").value);
      if (edited === null || typeof edited !== "object"
          || Array.isArray(edited)) throw new Error("must be a JSON object");
      if (dismissKeyHandler) document.removeEventListener("keydown", dismissKeyHandler);
      answer({ decision: "edit", edited_args: edited });
      // server re-sends an updated permission_request; nothing else to do
    } catch (err) {
      alert(`bad edit: ${err.message}`);
    }
  };
}

function showAskModal(env) {
  ui.modalId = env.id;
  const choices = env.choices || [];
  const rows = choices.map((choice, i) => `
    <label class="choice-row">
      <input type="radio" name="ask-choice" value="${i}" ${i === 0 ? "checked" : ""}>
      ${esc(choice)}</label>`).join("");
  showModal(`
    <div class="kind-tag">the agent asks</div>
    <h3>${esc(env.question)}</h3>
    ${env.context ? `<div class="context-note">${esc(env.context)}</div>` : ""}
    ${rows ? `<div id="choices">${rows}</div>` : ""}
    <textarea id="ask-text" placeholder="${choices.length
      ? "or type your own answer…" : "your answer…"}"></textarea>
    <div class="modal-actions">
      <button class="m-btn primary" id="ask-send">send answer</button>
    </div>`);
  const commit = () => {
    let text;
    const picked = $("#modal input[name=ask-choice]:checked");
    const typed = $("#ask-text").value.trim();
    if (typed) text = typed;
    else if (picked) text = choices[Number(picked.value)];
    else return;
    send({ type: "answer", id: env.id, text });
  };
  $("#ask-send").onclick = commit;
  $("#ask-text").focus();
  $("#ask-text").onkeydown = (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); commit(); }
  };
}

/* A unified diff, one span per line, so added and removed lines read at a
   glance. Escaped line by line -- the text is a skill a model wrote. */
function diffLines(diff) {
  if (!diff) return "(no change to the files)";
  return diff.split("\n").map((line) => {
    const cls = line.startsWith("+++") || line.startsWith("---") ? "d-file"
      : line.startsWith("+") ? "d-add" : line.startsWith("-") ? "d-del"
      : line.startsWith("@@") ? "d-hunk" : "";
    return `<span class="${cls}">${esc(line)}</span>`;
  }).join("\n");
}

/* The save question for a learned skill (notes/96). Everything that would
   be written is on screen and editable; the default is no. Saving with an
   edit that breaks a rule comes back as the same question with the error
   on top, and the edit kept. */
function showLearnModal(env) {
  ui.modalId = env.id;
  const test = env.test || {};
  const tested = test.passed == null
    ? "no script, so nothing to run"
    : `passed (${test.runs} run${test.runs > 1 ? "s" : ""}) — $ ${test.command}`;
  const scopes = Object.entries(env.scopes || {}).map(([key, s]) => `
    <label class="choice-row" title="${esc(s.path)}">
      <input type="radio" name="learn-scope" value="${esc(key)}"
             ${key === env.scope ? "checked" : ""}>
      ${esc(s.label)}</label>`).join("");
  const update = !!env.repairs;
  showModal(`
    <div class="kind-tag">${update ? "update this skill?" : "save this as a skill?"}</div>
    <h3>${esc(env.name)}</h3>
    ${env.error ? `<div class="banner banner-error learn-error">${esc(env.error)}</div>` : ""}
    ${update ? `<div class="context-note">It failed this time — ${esc(env.failure)}.
      The task was then finished another way; this is that way, written over
      the saved recipe.</div>
      <pre class="learn-diff">${diffLines(env.diff)}</pre>` : ""}
    <div class="context-note">${esc(env.description)}</div>
    ${env.renamed_from ? `<div class="context-note">renamed: a skill you wrote is
      already called ${esc(env.renamed_from)}</div>` : ""}
    <dl class="learn-facts">
      <dt>needs</dt><dd>${esc(env.needs || "nothing")}${(env.connections || []).map((c) =>
        `<br>${esc(c.name)}: ${c.connected ? "connected" : c.known ? "not connected"
          : "no such connector in Setu here"}`).join("")}</dd>
      <dt>inputs</dt><dd>${esc(env.inputs || "none")}</dd>
      <dt>tested</dt><dd>${esc(tested)}</dd>
      ${env.tool ? `<dt>tool</dt><dd>${esc(env.tool.name)} ${env.tool.kept
        ? "stays — the fixed script takes the same arguments"
        : "goes — the fixed script's arguments changed; it is a recipe again "
          + "until it has worked 5 times in a row"}</dd>` : ""}
      <dt>cost</dt><dd>${fmtNum(env.spent?.input ?? 0)} in / ${fmtNum(env.spent?.output ?? 0)} out tokens to write and test</dd>
      ${env.replaces && !update ? `<dt>replaces</dt><dd>${esc(env.replaces)}</dd>` : ""}
    </dl>
    ${update ? "" : `<div class="learn-scope">${scopes}</div>`}
    <label class="learn-label" for="learn-md">SKILL.md${update ? " as proposed" : ""} — edit before saving if you like</label>
    <textarea id="learn-md" class="learn-text" spellcheck="false">${esc(env.skill_md)}</textarea>
    ${env.script_name ? `
      <label class="learn-label" for="learn-script">${esc(env.script_name)}</label>
      <textarea id="learn-script" class="learn-text" spellcheck="false">${esc(env.script)}</textarea>` : ""}
    ${test.output ? `<details><summary>test output</summary>
      <pre class="args">${esc(test.output)}</pre></details>` : ""}
    <div class="modal-actions">
      <button class="m-btn" data-act="no">no</button>
      <button class="m-btn primary" data-act="save">${update ? "update skill" : "save skill"}</button>
    </div>`);
  $("#modal").onclick = (e) => {
    const act = e.target?.closest?.("[data-act]")?.dataset?.act;
    if (!act) return;
    if (act === "no") { send({ type: "answer", id: env.id, decision: "no" }); return; }
    send({
      type: "answer", id: env.id, decision: "save",
      scope: $("#modal input[name=learn-scope]:checked")?.value ?? env.scope,
      skill_md: $("#learn-md").value,
      script: $("#learn-script") ? $("#learn-script").value : null,
    });
  };
}

function showToolModal(env) {
  ui.modalId = env.id;
  const test = env.test || {};
  const params = Object.entries(env.parameters?.properties || {}).map(([key, p]) => `
    <dt>${esc(key)}</dt><dd>${esc(p.type)}, ${(env.parameters.required || [])
      .includes(key) ? "required" : "optional"}${p.description
      ? ` — ${esc(p.description)}` : ""}</dd>`).join("");
  showModal(`
    <div class="kind-tag">make this a tool?</div>
    <h3>${esc(env.name)}</h3>
    <div class="context-note">${esc(env.description)}</div>
    <dl class="learn-facts">
      <dt>from</dt><dd>the skill ${esc(env.skill)}</dd>
      ${params}
      <dt>runs</dt><dd><code>${esc(env.command)}</code></dd>
      <dt>tested</dt><dd>${test.passed ? "passed" : "FAILED"} — ${esc(JSON.stringify(test.args))}</dd>
      <dt>cost</dt><dd>${fmtNum(env.spent?.input ?? 0)} in / ${fmtNum(env.spent?.output ?? 0)} out tokens to write it</dd>
    </dl>
    <div class="context-note">Each call still asks first, like bash.</div>
    ${test.output ? `<details ${test.passed ? "" : "open"}><summary>test output</summary>
      <pre class="args">${esc(test.output)}</pre></details>` : ""}
    <details><summary>tool.json</summary><pre class="args">${esc(env.tool_json)}</pre></details>
    <div class="modal-actions">
      <button class="m-btn" data-act="no">no</button>
      ${test.passed ? '<button class="m-btn primary" data-act="make">make it a tool</button>' : ""}
    </div>`);
  $("#modal").onclick = (e) => {
    const act = e.target?.closest?.("[data-act]")?.dataset?.act;
    if (!act) return;
    send({ type: "answer", id: env.id, decision: act === "make" ? "make" : "no" });
  };
}

/* ---------- small dialogs and menus ----------
   The browser's prompt()/confirm() are the one place this UI used to fall
   out of its own skin. Same flows, same payloads -- just rendered here, on
   their own overlay layer so an approval modal can still land on top. */

let dialogEscape = null;

function closeDialog() {
  $("#dialog-backdrop").classList.add("hidden");
  $("#dialog").textContent = "";
  if (dialogEscape) {
    document.removeEventListener("keydown", dialogEscape, true);
    dialogEscape = null;
  }
}

/* Resolves to the trimmed string (or `true` for a confirm), or null when
   the operator backs out -- cancel means cancel. */
function openDialog({ title, body, value, placeholder,
                      confirm: confirmText = "ok", tone = "primary",
                      input: wantsInput = true }) {
  return new Promise((resolve) => {
    const box = $("#dialog");
    box.innerHTML = `<h3>${esc(title)}</h3>`
      + (body ? `<p class="dialog-body">${esc(body)}</p>` : "")
      + (wantsInput ? `<input id="dialog-input" spellcheck="false">` : "")
      + `<div class="modal-actions">`
      + `<button class="m-btn" data-act="cancel">cancel</button>`
      + `<button class="m-btn ${tone === "danger" ? "solid-danger" : "primary"}"`
      + ` data-act="ok">${esc(confirmText)}</button></div>`;
    $("#dialog-backdrop").classList.remove("hidden");

    const field = $("#dialog-input");
    if (field) {
      field.value = value ?? "";
      if (placeholder) field.placeholder = placeholder;
      field.focus();
      field.select();
    } else {
      box.querySelector('[data-act="ok"]').focus();
    }

    const done = (out) => { closeDialog(); resolve(out); };
    box.onclick = (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "ok") done(field ? field.value.trim() : true);
      else if (act === "cancel") done(null);
    };
    if (field) {
      field.onkeydown = (e) => {
        if (e.key === "Enter") { e.preventDefault(); done(field.value.trim()); }
      };
    }
    // capture phase + stopPropagation: esc closes THIS dialog and must not
    // also reach the document handler that cancels a running turn.
    dialogEscape = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      done(null);
    };
    document.addEventListener("keydown", dialogEscape, true);
    $("#dialog-backdrop").onclick = (e) => {
      if (e.target === $("#dialog-backdrop")) done(null);
    };
  });
}

const confirmDialog = (opts) => openDialog({ ...opts, input: false });

let menuEscape = null;

function closeMenu() {
  document.querySelector("#menu-layer")?.remove();
  if (menuEscape) {
    document.removeEventListener("keydown", menuEscape, true);
    menuEscape = null;
  }
}

/* A popover anchored under a chip: a fixed roster reads better as a list
   than as a free-text box you have to spell correctly. */
function openMenu(anchor, { label, items }) {
  closeMenu();
  const layer = document.createElement("div");
  layer.id = "menu-layer";
  const menu = document.createElement("div");
  menu.className = "menu";
  menu.setAttribute("role", "menu");
  if (label) {
    const cap = document.createElement("div");
    cap.className = "menu-label";
    cap.textContent = label;
    menu.append(cap);
  }
  for (const item of items) {
    const b = document.createElement("button");
    b.className = "menu-item";
    b.setAttribute("role", "menuitemradio");
    b.setAttribute("aria-checked", item.checked ? "true" : "false");
    b.innerHTML = `<span>${esc(item.name)}</span>`
      + `<span class="tick">${icon("check", "ico-sm", 2.4)}</span>`;
    b.onclick = () => { closeMenu(); item.onPick(); };
    menu.append(b);
  }
  layer.append(menu);
  layer.onclick = (e) => { if (e.target === layer) closeMenu(); };
  document.body.append(layer);

  const r = anchor.getBoundingClientRect();
  menu.style.top = `${Math.min(r.bottom + 6,
    window.innerHeight - menu.offsetHeight - 8)}px`;
  menu.style.left = `${Math.max(8, Math.min(r.left,
    window.innerWidth - menu.offsetWidth - 8))}px`;
  menu.querySelector(".menu-item")?.focus();

  menuEscape = (e) => {
    if (e.key !== "Escape") return;
    e.preventDefault();
    e.stopPropagation();
    closeMenu();
  };
  document.addEventListener("keydown", menuEscape, true);
}

/* ---------- appearance ---------- */

const THEMES = ["system", "light", "dark"];

function currentTheme() {
  try { return localStorage.getItem("yantra-theme") || "system"; }
  catch { return "system"; }
}

function applyTheme(name) {
  if (name === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = name;
  try { localStorage.setItem("yantra-theme", name); } catch { /* private mode */ }
  $("#btn-theme").title = `appearance: ${name} — click to cycle system ⇄ light ⇄ dark`;
}

$("#btn-theme").onclick = () => {
  const next = THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length];
  applyTheme(next);
  toast(`appearance: ${next}`);
};
applyTheme(currentTheme());

/* ---------- composer ---------- */

const input = $("#input");

input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});
input.addEventListener("input", autoGrow);
function autoGrow() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 200) + "px";
}

$("#btn-send").onclick = sendMessage;
$("#btn-cancel").onclick = () => send({ type: "cancel" });
$("#btn-image").onclick = () => $("#file-input").click();

$("#file-input").onchange = async () => {
  for (const file of $("#file-input").files) {
    if (ui.staged.length >= 5) { toast("5 images max per message"); break; }
    try {
      const buf = await file.arrayBuffer();
      if (buf.byteLength > 5 * 1024 * 1024) { toast(`${file.name}: over 5 MB`); continue; }
      ui.staged.push({
        filename: file.name,
        data_base64: b64FromBuffer(buf),
        url: URL.createObjectURL(file),
      });
    } catch { toast(`could not read ${file.name}`); }
  }
  $("#file-input").value = "";
  renderStaged();
};

function b64FromBuffer(buf) {
  const bytes = new Uint8Array(buf);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

function renderStaged() {
  const wrap = $("#staged");
  wrap.classList.toggle("hidden", !ui.staged.length);
  wrap.textContent = "";
  ui.staged.forEach((img, i) => {
    const chip = document.createElement("span");
    chip.className = "staged-chip";
    const thumb = document.createElement("img");
    thumb.src = img.url;
    chip.append(thumb, document.createTextNode(img.filename));
    const x = document.createElement("button");
    x.textContent = "×";
    x.title = "remove";
    x.onclick = () => { ui.staged.splice(i, 1); renderStaged(); };
    chip.append(x);
    wrap.append(chip);
  });
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text || ui.turnActive) return;
  const body = { text, images: ui.staged.map(({ filename, data_base64 }) =>
    ({ filename, data_base64 })) };
  try {
    const res = await fetch("/api/message", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.status === 409) { toast("a turn is already running"); return; }
    if (!res.ok) {
      toast((await res.json().catch(() => ({}))).detail || "failed to send");
      return;
    }
  } catch { toast("offline — not sent"); return; }
  addUserMessage(text, ui.staged.length);
  ui.staged.forEach((img) => URL.revokeObjectURL(img.url));
  ui.staged = [];
  renderStaged();
  input.value = "";
  autoGrow();
  beginTurn();
  setStatus("waiting for first token…");
}

/* ---------- header controls ---------- */

async function post(path, payload = {}) {
  const res = await fetch(path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { toast(data.detail || res.statusText); return null; }
  return data;
}

$("#chip-model").onclick = async () => {
  const slug = await openDialog({
    title: "switch model",
    body: "the slug this provider knows it by — pricing and the context "
      + "window follow the slug, so an unknown one simply shows no figure.",
    value: $("#model-name").textContent,
    placeholder: "model slug",
    confirm: "switch",
  });
  if (slug) post("/api/model", { model: slug }).then((s) => s && applyHeader(s));
};
// esc stops the run — same as the red button. Deliberately NOT bound while
// a permission/ask modal is up: there, esc must not silently deny anything.
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && ui.turnActive && ui.modalId === null) {
    e.preventDefault();
    send({ type: "cancel" });
  }
});
// Permission mode: flip ask <-> yolo. Allowed mid-turn on purpose -- the
// server applies it to every not-yet-approved call of the running turn.
$("#chip-mode").onclick = () => {
  const next = $("#mode-label").textContent === "yolo" ? "ask" : "yolo";
  post("/api/permissions", { mode: next }).then((s) => {
    if (!s) return;
    applyHeader(s);
    toast(next === "yolo"
      ? "yolo on — tools run without asking"
      : "permission prompts back on");
  });
};
// Session awareness: cycle off -> local -> full -> off. Mid-turn allowed on
// purpose -- the recomposed system lands on the turn's next model call.
const ENV_CYCLE = { off: "local", local: "full", full: "off" };
$("#chip-env").onclick = () => {
  const next = ENV_CYCLE[$("#env-label").textContent] || "local";
  post("/api/env-context", { mode: next }).then((s) => {
    if (!s) return;
    applyHeader(s);
    toast(`env context: ${next}`);
  });
};
const PROVIDERS = ["anthropic", "openai", "responses", "ollama"];
$("#chip-provider").onclick = (e) => {
  const live = $("#provider-name").textContent;
  openMenu(e.currentTarget, {
    label: "provider",
    items: PROVIDERS.map((name) => ({
      name,
      checked: name === live,
      // the server resets the model slug to the new provider's default:
      // slugs are per-provider namespaces
      onPick: () => post("/api/provider", { provider: name })
        .then((st) => st && applyHeader(st)),
    })),
  });
};
$("#btn-save").onclick = async () => {
  const name = await openDialog({
    title: "checkpoint this session",
    body: "history, model, provider and the tool roster, written to disk "
      + "under this name. Saving again bumps the version.",
    value: "default",
    placeholder: "default",
    confirm: "save",
  });
  if (name === null) return;
  const d = await post("/api/save", { name: name || "default" });
  if (d) toast(`saved '${d.saved}' v${d.version}`);
};
$("#btn-load").onclick = async () => {
  const name = await openDialog({
    title: "restore a checkpoint",
    body: "replaces the current history and session settings with the "
      + "saved ones. Anything unsaved here is gone.",
    value: "default",
    placeholder: "default",
    confirm: "restore",
  });
  if (name === null) return;
  await lookBack(true);   // switching conversations ends this one
  const d = await post("/api/load", { name: name || "default" });
  if (d) {
    applyHeader(d);
    transcript.textContent = "";
    forgetAssistant();
  ui.currentThinking = ui.openToolCard = null;
    await fetchHistory();
    toast("restored");
  }
};
$("#btn-compact").onclick = async () => {
  const d = await post("/api/compact");
  if (d) {
    applyHeader(d);
    const st = d.stats;
    toast(`compacted ${st.messages_before} → ${st.messages_after} message(s)`
          + (st.masked ? `, elided ${st.masked} tool result(s)` : ""));
  }
};
$("#btn-clear").onclick = async () => {
  const ok = await confirmDialog({
    title: "clear conversation history?",
    body: "the transcript and the model's memory of this session both go. "
      + "Checkpoints already on disk are untouched.",
    confirm: "clear history",
    tone: "danger",
  });
  if (!ok) return;
  await lookBack(true);
  const d = await post("/api/clear");
  if (d) {
    applyHeader(d);
    transcript.textContent = "";
    forgetAssistant();
  ui.currentThinking = ui.openToolCard = null;
    toast("history cleared");
  }
};

/* ---------- memory panel ----------

   What is remembered about the person, newest first; the ones this
   conversation's prompt carries are marked. Forget and add work without
   a model turn, and apply from the next conversation (the prompt layer
   is filled once, so the cached prefix survives). */

const memPanel = { data: null };

$("#chip-mem").onclick = openMemPanel;
$("#mem-close").onclick = () => $("#mem-backdrop").classList.add("hidden");
$("#mem-backdrop").addEventListener("click", (e) => {
  if (e.target === $("#mem-backdrop")) $("#mem-backdrop").classList.add("hidden");
});

async function openMemPanel() {
  $("#mem-backdrop").classList.remove("hidden");
  $("#mem-body").innerHTML = '<div class="panel-loading">loading…</div>';
  const res = await fetch("/api/memory");
  const data = await res.json();
  if (!res.ok) {
    $("#mem-body").innerHTML = `<div class="conn-box warn"><span>${esc(data.detail)}</span></div>`;
    return;
  }
  memPanel.data = data;
  renderMemory();
}

async function memoryPost(path, body) {
  const res = await fetch(path, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) { addBanner(`memory: ${data.detail}`, true); return false; }
  memPanel.data = data;
  renderMemory();
  return true;
}

function renderMemory() {
  const d = memPanel.data;
  const body = $("#mem-body");
  body.textContent = "";
  const form = document.createElement("form");
  form.className = "mem-add";
  const input = document.createElement("input");
  input.placeholder = "tell it something to keep — e.g. I live near RDU";
  input.maxLength = 300;
  const add = document.createElement("button");
  add.className = "m-btn primary";
  add.textContent = "remember";
  form.append(input, add);
  form.onsubmit = async (e) => {
    e.preventDefault();
    if (!input.value.trim()) return;
    if (await memoryPost("/api/memory/add", { statement: input.value })) {
      toast("remembered — from the next conversation on");
    }
  };
  body.append(form);
  const look = document.createElement("button");
  look.className = "m-btn mem-look";
  look.textContent = "remember from this conversation";
  look.title = "look back over this conversation for facts about you; "
    + "you keep or drop each";
  look.onclick = async () => {
    $("#mem-backdrop").classList.add("hidden");
    await lookBack(false);
  };
  body.append(look);
  section(body, `${d.items.length} remembered`,
    `${d.store} store, for ${d.user} · ● = in this conversation's prompt`);
  if (d.notice) {
    const warn = document.createElement("div");
    warn.className = "conn-box warn";
    warn.innerHTML = `<span>${esc(d.notice)}</span>`;
    body.append(warn);
  }
  const cannot = d.cannot || [];
  if (cannot.length) {
    // A store behind an MCP server may lack a tool for some verbs.
    const gap = document.createElement("div");
    gap.className = "conn-foot";
    gap.textContent = `the ${d.store} store cannot ${cannot.join(" or ")}`
      + (cannot.includes("list") ? " — so what it keeps is not shown here" : "");
    body.append(gap);
  }
  if (!d.items.length && !cannot.includes("list")) {
    const p = document.createElement("div");
    p.className = "conn-foot";
    p.textContent = "nothing yet — tell the agent about yourself, and it "
      + "asks before keeping anything.";
    body.append(p);
  }
  for (const item of d.items) {
    const row = document.createElement("div");
    row.className = "conn-card mem-row";
    const main = document.createElement("div");
    main.innerHTML = `<div class="conn-title">${item.in_prompt ? "● " : ""}${esc(item.statement)}</div>
      <div class="conn-sub">#${esc(item.id)}${item.kind ? " · " + esc(item.kind) : ""}
        ${item.package ? " · from " + esc(item.package) : ""}
        ${item.created ? " · " + esc(item.created.slice(0, 10)) : ""}</div>`;
    const actions = document.createElement("div");
    actions.className = "conn-actions";
    if (!cannot.includes("forget")) {
      const drop = document.createElement("button");
      drop.className = "m-btn danger";
      drop.textContent = "forget";
      drop.onclick = async () => {
        if (await memoryPost("/api/memory/forget", { id: item.id })) toast(`forgot #${item.id}`);
      };
      actions.append(drop);
    }
    row.append(main, actions);
    body.append(row);
  }
}

/* ---------- the look back (memory/reflect.py) ----------

   When a conversation ends here -- clear, or restoring another -- the
   same model looks back over it for facts about the person said in
   passing. Each candidate is shown unticked: nothing is kept without a
   yes. `ending` follows --reflect (off: nothing, auto: kept without
   asking); the panel's button always looks, and always asks. */

async function lookBack(ending) {
  if (!ui.lastState?.memory) return;
  setStatus("· looking back over the conversation");
  const res = await fetch("/api/memory/reflect", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ending }),
  });
  hideStatus();
  const data = await res.json().catch(() => ({}));
  if (!res.ok) { addBanner(data.detail || "looking back failed", false, true); return; }
  if (data.kept) toast(`remembered ${data.kept} about you`);
  if (data.candidates?.length) await offerMemories(data.candidates);
  else if (!ending) toast("nothing new worth remembering in this conversation");
}

function offerMemories(candidates) {
  return new Promise((resolve) => {
    const box = $("#dialog");
    box.innerHTML = `<h3>worth remembering about you?</h3>
      <p class="dialog-body">From this conversation, for later ones. Tick
        what is true and will stay true; the rest is dropped.</p>
      <div class="mem-offer">${candidates.map((c, i) => `
        <label><input type="checkbox" data-i="${i}">
          <span>${esc(c.statement)} <span class="kind">${esc(c.kind)}</span></span>
        </label>`).join("")}</div>
      <div class="modal-actions">
        <button class="m-btn" data-act="none">keep none</button>
        <button class="m-btn primary" data-act="keep">keep ticked</button>
      </div>`;
    $("#dialog-backdrop").classList.remove("hidden");
    const done = async (keepTicked) => {
      const ticked = new Set([...box.querySelectorAll("input:checked")]
        .map((el) => Number(el.dataset.i)));
      const keep = keepTicked ? candidates.filter((_, i) => ticked.has(i)) : [];
      const drop = candidates.filter((c) => !keep.includes(c));
      closeDialog();
      const d = await post("/api/memory/keep", { keep, drop });
      if (d?.kept) toast(`remembered ${d.kept} — from the next conversation on`);
      resolve();
    };
    box.onclick = (e) => {
      const act = e.target.closest("[data-act]")?.dataset.act;
      if (act === "keep") done(true);
      else if (act === "none") done(false);
    };
    dialogEscape = (e) => {
      if (e.key !== "Escape") return;
      e.preventDefault();
      e.stopPropagation();
      done(false);
    };
    document.addEventListener("keydown", dialogEscape, true);
    $("#dialog-backdrop").onclick = null;
  });
}

/* ---------- connections panel (notes/99) ----------

   Setu's report, drawn as cards: accounts connected (with their level,
   their tools, Change access and Disconnect), then connectors installed
   but not connected (pick a level, name the account, Connect). A sign-in
   is Setu's: the server runs `setu connect --json`, and this page only
   shows the address Google's sign-in lives at. It works when this page is
   open on the computer running Yantra; anywhere else the card shows the
   command to run there instead. */

const conn = { loaded: false, data: null, signin: null };

$("#chip-conn").onclick = openConnPanel;
$("#conn-close").onclick = () => $("#conn-backdrop").classList.add("hidden");
$("#conn-backdrop").addEventListener("click", (e) => {
  if (e.target === $("#conn-backdrop")) $("#conn-backdrop").classList.add("hidden");
});
$("#conn-refresh").onclick = async () => {
  const res = await fetch("/api/connections/refresh", { method: "POST" });
  if (!res.ok) { addBanner(`cannot refresh: ${(await res.json()).detail}`, true); return; }
  applyConnections(await res.json());
  toast("asked Setu again");
};

async function loadConnections() {
  conn.loaded = true;
  try {
    const res = await fetch("/api/connections");
    if (res.ok) applyConnections(await res.json());
  } catch { /* the chip just stays hidden */ }
}

function applyConnections(data) {
  // A broadcast carries no "local" (it is the server's, not this tab's).
  conn.data = { ...data, local: data.local ?? conn.data?.local ?? false };
  // A sign-in still waiting is news to any tab; one that ended before this
  // tab saw it is not -- its outcome was told to the tabs that were open.
  if (data.signin?.running) conn.signin = data.signin;
  const chip = $("#chip-conn");
  chip.classList.toggle("hidden", data.mode === "off");
  $("#conn-count").textContent = data.found
    ? `${data.connections.length} connected` : "setu";
  if (!$("#conn-backdrop").classList.contains("hidden")) renderConnections();
}

function onConnections(env) {
  applyConnections(env);
  const sync = env.sync || {};
  const added = Object.entries(sync.connected || {});
  if (sync.dropped?.length) toast(`${sync.dropped.join(", ")} disconnected — its tools are gone`);
  for (const note of sync.notes || []) addBanner(`setu: ${note}`, false, true);
  if (added.length && conn.justConnected) {
    const [name, count] = added.find(([n]) => n === conn.justConnected) || added[0];
    addBanner(`${name} is connected — ${count} tool(s) from the next message on`, false);
    conn.justConnected = null;
  }
}

function onSignin(env) {
  // "done" only closes the sign-in: the outcome before it (connected,
  // error, cancelled) is what the box keeps saying.
  conn.signin = env.event === "done"
    ? { ...(conn.signin || {}), ref: env.ref, running: false }
    : { ...(conn.signin || {}), ...env,
        running: !["connected", "error", "cancelled"].includes(env.event) };
  if (env.event === "connected") {
    conn.justConnected = env.ref.replace(":", "-");
    toast(`signed in: ${env.ref}${env.email ? ` as ${env.email}` : ""}`);
  }
  if (env.event === "error") addBanner(`sign-in failed: ${env.message}`, true);
  if (!$("#conn-backdrop").classList.contains("hidden")) renderConnections();
}

async function openConnPanel() {
  $("#conn-backdrop").classList.remove("hidden");
  $("#conn-body").innerHTML = '<div class="panel-loading">loading…</div>';
  await loadConnections();
  renderConnections();
}

function renderConnections() {
  const body = $("#conn-body");
  const d = conn.data;
  body.textContent = "";
  if (!d) { body.innerHTML = '<div class="panel-loading">loading…</div>'; return; }
  if (d.mode === "off") {
    body.innerHTML = '<div class="conn-box warn"><span>Setu is off for this session '
      + '(<code>--no-setu</code>). Start Yantra without it to see your connections.</span></div>';
    return;
  }
  if (!d.found) {
    body.innerHTML = `<div class="conn-box warn"><span>${esc(d.error
      || "Setu was not found.")} Install Setu, or start Yantra with
      <code>--setu /path/to/setu</code>, then press refresh.</span></div>`;
    return;
  }
  body.append(signinBox());
  const setupBox = clientFileBox(d);
  if (setupBox) body.append(setupBox);

  const names = Object.fromEntries(d.connectors.map((c) => [c.id, c]));
  section(body, "connected", d.connections.length
    ? "the agent has these accounts' tools this session"
    : "nothing yet — connect an account below");
  for (const row of d.connections) body.append(connectedCard(row, names[row.connector]));

  section(body, "available", "installed on this computer; connect as many accounts as you like");
  if (!d.connectors.length) {
    const none = document.createElement("div");
    none.className = "mcp-empty";
    none.textContent = "no connectors installed — e.g. uv pip install setu-gmail";
    body.append(none);
  }
  for (const c of d.connectors) body.append(connectorCard(c, d));

  for (const problem of d.problems || []) {
    const p = document.createElement("div");
    p.className = "conn-box warn";
    p.innerHTML = `<span>${esc(problem)}</span>`;
    body.append(p);
  }
  const foot = document.createElement("div");
  foot.className = "conn-foot";
  foot.textContent = `Setu ${d.version || ""} · signed in on this computer · `
    + "keys stay with Setu · nothing is sent to us";
  body.append(foot);
}

function section(body, title, note) {
  const head = document.createElement("div");
  head.className = "section-head";
  head.innerHTML = `<div><h4>${esc(title)}</h4><span class="tools-note">${esc(note)}</span></div>`;
  body.append(head);
}

function signinBox() {
  const box = document.createElement("div");
  const s = conn.signin;
  if (!s || (!s.running && !["error", "cancelled"].includes(s.event))) return box;
  if (s.running) {
    box.className = "conn-box";
    box.innerHTML = `<span>Waiting for you to sign in to <b>${esc(s.ref)}</b>.
      ${s.url ? "Open the sign-in, allow access, then come back here."
              : "Starting…"}</span>`;
    if (s.url) {
      const a = document.createElement("a");
      a.className = "m-btn primary";
      a.href = s.url; a.target = "_blank"; a.rel = "noopener";
      a.textContent = "open the sign-in";
      box.append(a);
    }
    const cancel = document.createElement("button");
    cancel.className = "m-btn";
    cancel.textContent = "cancel";
    cancel.onclick = () => fetch("/api/connections/cancel", { method: "POST" });
    box.append(cancel);
  } else {
    box.className = "conn-box " + (s.event === "error" ? "bad" : "warn");
    box.innerHTML = `<span>${s.event === "error"
      ? `Sign-in to ${esc(s.ref || "")} failed: ${esc(s.message || "")}`
      : `Sign-in to ${esc(s.ref || "")} cancelled — nothing was saved.`}</span>`;
  }
  return box;
}

function clientFileBox(d) {
  const needs = d.connectors.filter((c) => !c.ready);
  if (!needs.length) return null;
  const box = document.createElement("div");
  box.className = "conn-box warn";
  box.innerHTML = `<span>${esc(needs.map((c) => c.name).join(", "))}: signing in to Google
    needs your "Desktop app" OAuth client file for now. Setu remembers where it is
    (never its contents) — the Setu README shows how to make one.</span>`;
  const row = document.createElement("div");
  row.className = "conn-setup";
  const input = document.createElement("input");
  input.placeholder = "/path/to/client_secret_….json";
  input.spellcheck = false;
  const save = document.createElement("button");
  save.className = "m-btn primary";
  save.textContent = "use this file";
  save.onclick = async () => {
    save.disabled = true;
    try {
      const res = await fetch("/api/connections/client-file", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: input.value }) });
      const out = await res.json();
      if (!res.ok) { addBanner(`not used: ${out.detail}`, true); return; }
      applyConnections(out);
      toast("Setu will use that client file");
    } finally { save.disabled = false; }
  };
  row.append(input, save);
  box.append(row);
  return box;
}

function levelSelect(c, current) {
  const sel = document.createElement("select");
  for (const lv of c?.levels || []) {
    const o = document.createElement("option");
    o.value = lv.name; o.textContent = lv.label;
    o.title = lv.description || "";
    if (lv.name === (current || c.default_level)) o.selected = true;
    sel.append(o);
  }
  return sel;
}

function connectedCard(row, c) {
  const card = document.createElement("div");
  card.className = "conn-card";
  const dot = document.createElement("span");
  dot.className = "dot" + (row.running ? "" : " dead");
  dot.title = row.running ? "its server is running this session" : "its server is not running";
  const main = document.createElement("div");
  main.innerHTML = `<div class="conn-title">${esc(c?.name || row.connector)}
      <span class="t-badge">${esc(row.account)}</span></div>
    <div class="conn-sub">${row.email ? esc(row.email) + " · " : ""}<b>${esc(row.level_label || row.level)}</b>
      · ${row.tools} tool(s) as <code>mcp__${esc(row.server)}__…</code>
      ${row.last_used ? " · last used " + esc(String(row.last_used).slice(0, 10)) : ""}</div>`;
  const actions = document.createElement("div");
  actions.className = "conn-actions";
  const change = document.createElement("button");
  change.className = "m-btn";
  change.textContent = "change access";
  const drop = document.createElement("button");
  drop.className = "m-btn danger";
  drop.textContent = "disconnect";
  drop.onclick = () => disconnect(row);
  actions.append(change, drop);
  card.append(dot, main, actions);

  const form = document.createElement("div");
  form.className = "conn-form hidden";
  const sel = levelSelect(c, row.level);
  const go = document.createElement("button");
  go.className = "m-btn primary";
  go.textContent = "sign in again at this level";
  go.onclick = () => startSignin(row.connector, row.account, sel.value);
  form.append(sel, go);
  change.onclick = () => form.classList.toggle("hidden");
  card.append(form);
  if (!conn.data.local) card.append(commandLine(row.connector, row.account, null));
  return card;
}

function connectorCard(c, d) {
  const card = document.createElement("div");
  card.className = "conn-card";
  const dot = document.createElement("span");
  dot.className = "dot dead";
  const main = document.createElement("div");
  main.innerHTML = `<div class="conn-title">${esc(c.name)}
      ${c.connected ? '<span class="t-badge">connected</span>' : ""}</div>
    <div class="conn-sub">${esc(c.summary || "")}</div>`;
  card.append(dot, main, document.createElement("div"));

  const form = document.createElement("div");
  form.className = "conn-form";
  const account = document.createElement("input");
  const taken = new Set(d.connections.filter((r) => r.connector === c.id).map((r) => r.account));
  account.value = taken.has("personal") ? "work" : "personal";
  account.title = "your name for this account — tools are named after it";
  account.spellcheck = false;
  const sel = levelSelect(c, null);
  const note = document.createElement("div");
  note.className = "conn-level-note";
  const describe = () => {
    const lv = c.levels.find((l) => l.name === sel.value);
    note.textContent = lv?.description || "";
  };
  sel.onchange = describe;
  describe();
  const go = document.createElement("button");
  go.className = "m-btn primary";
  go.textContent = c.connected ? "connect another" : "connect";
  go.disabled = !c.ready || !d.local || Boolean(conn.signin?.running);
  go.title = !c.ready ? c.not_ready
    : !d.local ? "signing in works from a page on the computer running Yantra"
    : "opens the site's own sign-in; you choose there";
  go.onclick = () => startSignin(c.id, account.value.trim() || "personal", sel.value);
  form.append(account, sel, go);
  card.append(form, note);
  if (!d.local) card.append(commandLine(c.id, account.value, sel.value));
  return card;
}

function commandLine(connector, account, level) {
  const line = document.createElement("div");
  line.className = "conn-cmd";
  line.innerHTML = `This page is not on the computer running Yantra. There, run
    <code>setu connect ${esc(connector)} --as ${esc(account)}${level
      ? " --level " + esc(level) : ""}</code>, then press refresh.`;
  return line;
}

async function startSignin(connector, account, level) {
  const res = await fetch("/api/connections/connect", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ connector, account, level }) });
  const out = await res.json();
  if (!res.ok) { addBanner(`cannot sign in: ${out.detail}`, true); return; }
  conn.signin = { ref: out.ref, running: true, event: "starting" };
  renderConnections();
}

async function disconnect(row) {
  const ok = await openDialog({
    title: `disconnect ${row.ref}?`,
    body: `Setu revokes the key at the provider and forgets it; the agent loses `
      + `${row.tools} tool(s). You can connect again any time.`,
    confirm: "disconnect", tone: "danger", input: false });
  if (!ok) return;
  const res = await fetch("/api/connections/disconnect", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ ref: row.ref }) });
  const out = await res.json();
  if (!res.ok) { addBanner(`not disconnected: ${out.detail}`, true); return; }
  applyConnections(out);
  toast(`${row.ref} disconnected`);
}

/* ---------- tools panel ---------- */

/* Two sections: mcp servers on top (connect new ones, soft-toggle or
   remove existing), then every registered tool with a live switch each.
   Toggles POST immediately and are safe mid-turn: the loop consults the
   registry per call, so pulling `bash` cuts the very next bash call of a
   running turn. The panel sits UNDER the approval modal on purpose -- an
   approval can still pop over it while you're in here. */

$("#chip-tools").onclick = openToolsPanel;
$("#tools-close").onclick = closeToolsPanel;
$("#tools-backdrop").addEventListener("click", (e) => {
  if (e.target === $("#tools-backdrop")) closeToolsPanel();
});

async function openToolsPanel() {
  $("#tools-backdrop").classList.remove("hidden");
  const rows = $("#tools-rows");
  rows.innerHTML = '<div class="panel-loading">loading…</div>';

  let servers = null, tools = null, skills = null;
  try {
    const [mcpRes, toolRes, skillRes] = await Promise.all([
      fetch("/api/mcp"), fetch("/api/tools"), fetch("/api/skills")]);
    // A session without an mcp manager answers 400 -- hide the section.
    servers = mcpRes.ok ? (await mcpRes.json()).servers : null;
    tools = toolRes.ok ? await toolRes.json() : null;
    // Same rule for skills: --no-skills answers 400 -> section hidden.
    skills = skillRes.ok ? await skillRes.json() : null;
  } catch { /* all stay null -> per-section error text */ }

  $("#mcp-section").classList.toggle("hidden", servers === null);
  collapseAddForm();
  renderMCPRows(servers);
  $("#skills-section").classList.toggle("hidden", skills === null);
  closeSkillForm();
  renderSkillRows(skills);

  if (!tools) {
    rows.innerHTML = '<div class="mcp-empty">could not load tools</div>';
    return;
  }
  rows.textContent = "";
  for (const t of tools) rows.append(toolRow(t));
}

/* ---- skills ---- */

$("#skills-reload").onclick = async () => {
  const btn = $("#skills-reload");
  btn.disabled = true;
  try {
    const res = await fetch("/api/skills/reload", { method: "POST" });
    if (!res.ok) return;                       // mid-turn: 409, just no-op
    const skills = await (await fetch("/api/skills")).json();
    renderSkillRows(skills);
  } catch { /* leave the rows as they were */ }
  finally { btn.disabled = false; }
};

function renderSkillRows(skills) {
  const rows = $("#skills-rows");
  if (!skills) { rows.textContent = ""; return; }
  rows.textContent = "";
  if (!skills.skills.length && !skills.broken.length) {
    rows.innerHTML = '<div class="mcp-empty">no skills yet — press '
      + '＋ new to write one, or drop a skills/&lt;name&gt;/SKILL.md '
      + 'into this project</div>';
    return;
  }
  for (const s of skills.skills) rows.append(skillRow(s));
  for (const b of skills.broken) {
    const row = document.createElement("div");
    row.className = "mcp-row off";
    row.innerHTML = `<span class="dot dead"></span>`
      + `<div><span class="t-name">${esc(b.path)}</span></div>`
      + `<div class="t-desc">${esc(b.reason)}</div>`;
    rows.append(row);
  }
}

function skillRow(s) {
  const row = document.createElement("div");
  row.className = "mcp-row" + (s.enabled === false ? " off" : "");

  const dot = document.createElement("span");
  // A filled dot means the model actually pulled it this session -- the
  // first question every skill author has.
  dot.className = "dot" + (s.loaded ? "" : " dead");
  dot.title = s.loaded ? "loaded this session" : "not loaded yet";
  row.append(dot);

  const name = document.createElement("div");
  name.innerHTML = `<span class="t-name">${esc(s.name)}</span>`
    + `<span class="t-badge">${esc(s.source)}</span>`
    + (s.mode === "subagent" ? '<span class="t-ro">delegated</span>' : "")
    + (s.stale ? '<span class="t-ro" title="failed 3 times in a row; left out '
      + 'until a fresh solve repairs it">stale</span>' : "")
    + (s.tool ? `<span class="t-ro" title="${esc(s.tool_error
        || "its script is a tool the model can call in one step")}">tool: `
      + `${esc(s.tool)}${s.tool_error ? " (not offered)" : ""}</span>` : "");
  name.title = s.path;
  row.append(name);

  const desc = document.createElement("div");
  desc.className = "t-desc";
  desc.textContent = s.description;
  // A learned skill's record: counted by Yantra from what it saw, never
  // by the model (notes/96).
  const c = s.counters;
  if (c) {
    const tally = document.createElement("div");
    tally.className = "t-tally";
    tally.textContent = `learned ${c.since} · worked ${c.worked} · failed ${c.failed}`
      + (c.last_ok ? ` · last ok ${c.last_ok}` : "")
      + (c.in_a_row != null && c.failed ? ` · in a row ${c.in_a_row}` : "")
      + (c.failing ? ` · failing ${c.failing}` : "");
    desc.append(tally);
  }
  // The suggestion only -- promoting is the person's call (notes/98).
  if (s.suggest_tool) {
    const make = document.createElement("button");
    make.className = "t-edit t-promote";
    make.textContent = "make it a tool?";
    make.title = `worked ${c ? c.in_a_row : 5} times in a row — make its `
      + "script one tool call (tested and shown to you first)";
    make.onclick = async () => {
      make.disabled = true;
      try {
        const res = await fetch(`/api/skills/${encodeURIComponent(s.name)}/tool`,
                                { method: "POST" });
        if (!res.ok) addBanner(`cannot start: ${(await res.json()).detail}`, true);
      } finally { make.disabled = false; }
    };
    desc.append(make);
  }
  row.append(desc);

  // Same switch as a tool row: off = out of the roster, refuses to load.
  const sw = document.createElement("label");
  sw.className = "t-switch";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = s.enabled !== false;
  box.title = "off = the model is never told this skill exists";
  box.onchange = async () => {
    box.disabled = true;
    try {
      const res = await fetch("/api/skills", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: s.name, enabled: box.checked }),
      });
      // 409 = a turn is running; the roster edits the prompt, so it waits
      if (!res.ok) box.checked = !box.checked;
      else row.classList.toggle("off", !box.checked);
    } catch { box.checked = !box.checked; }
    finally { box.disabled = false; }
  };
  const edit = document.createElement("button");
  edit.className = "t-edit";
  edit.innerHTML = icon("pencil", "ico-sm", 1.7) + "<span>edit</span>";
  edit.title = "open this skill in the editor";
  edit.onclick = () => openSkillForm(s.name);
  row.append(edit);

  // The input is opacity:0 -- .knob IS the visible switch (style.css).
  const knob = document.createElement("span");
  knob.className = "knob";
  sw.append(box, knob);
  row.append(sw);

  return row;
}

/* ---- the skill editor ---- */

// Which skill is open, or null for a new one. Also gates the name field:
// renaming an existing skill would mean moving its folder, so the editor
// does not pretend to offer it.
let editingSkill = null;

function skillFormFields() {
  return {
    name: $("#skill-name"), desc: $("#skill-desc"), body: $("#skill-body"),
    tools: $("#skill-tools"), output: $("#skill-output"),
    iters: $("#skill-iters"), error: $("#skill-error"),
  };
}

function skillMode() {
  const picked = document.querySelector('input[name="skill-mode"]:checked');
  return picked ? picked.value : "inline";
}

function syncSkillMode() {
  $("#skill-delegated-only").classList.toggle("hidden", skillMode() !== "subagent");
}

for (const radio of document.querySelectorAll('input[name="skill-mode"]')) {
  radio.onchange = syncSkillMode;
}

async function openSkillForm(name) {
  const f = skillFormFields();
  editingSkill = name || null;
  f.error.classList.add("hidden");
  $("#skill-form").classList.remove("hidden");
  $("#skill-form").scrollIntoView({ block: "nearest" });
  f.name.disabled = Boolean(name);   // renaming = moving a folder; not here

  if (!name) {
    for (const el of [f.name, f.desc, f.body, f.tools, f.output, f.iters]) {
      el.value = "";
    }
    document.querySelector('input[name="skill-mode"][value="inline"]').checked = true;
    syncSkillMode();
    f.name.focus();
    return;
  }
  try {
    const res = await fetch(`/api/skills/${encodeURIComponent(name)}`);
    if (!res.ok) return;
    const s = await res.json();
    f.name.value = s.name;
    f.desc.value = s.description;
    f.body.value = s.body;
    f.tools.value = (s.allowed_tools || []).join(", ");
    const mode = s.mode === "subagent" ? "subagent" : "inline";
    document.querySelector(`input[name="skill-mode"][value="${mode}"]`).checked = true;
    f.output.value = s.output_format || "";
    f.iters.value = s.max_iterations || "";
    syncSkillMode();
    f.body.focus();
  } catch { /* leave the form as it was */ }
}

function closeSkillForm() {
  $("#skill-form").classList.add("hidden");
  editingSkill = null;
}

$("#skill-new-btn").onclick = () => openSkillForm(null);

$("#skill-learn-btn").onclick = async () => {
  // The offer itself arrives over the socket, like an approval does.
  const res = await fetch("/api/learn", { method: "POST" });
  if (!res.ok) {
    const detail = (await res.json().catch(() => ({}))).detail;
    toast(detail || "cannot save a skill right now");
    return;
  }
  closeToolsPanel();
};
$("#skill-cancel").onclick = closeSkillForm;

$("#skill-save").onclick = async () => {
  const f = skillFormFields();
  const name = (editingSkill || f.name.value).trim();
  const btn = $("#skill-save");
  f.error.classList.add("hidden");
  if (!name) { showSkillError("a name is required"); return; }

  const payload = {
    description: f.desc.value,
    body: f.body.value,
    mode: skillMode(),
    allowed_tools: f.tools.value.split(/[,\s]+/).filter(Boolean),
    output_format: f.output.value,
    max_iterations: Number(f.iters.value) || 0,
  };
  btn.disabled = true;
  try {
    const res = await fetch(`/api/skills/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      // 422 carries the loader's own message -- the useful one.
      const detail = await res.json().catch(() => ({}));
      showSkillError(res.status === 409
        ? "a turn is running — the roster is part of the prompt, so saving waits"
        : (detail.detail || `save failed (${res.status})`));
      return;
    }
    closeSkillForm();
    renderSkillRows(await (await fetch("/api/skills")).json());
  } catch (e) {
    showSkillError(String(e));
  } finally { btn.disabled = false; }
};

function showSkillError(text) {
  const box = $("#skill-error");
  box.textContent = text;
  box.classList.remove("hidden");
}

/* ---- mcp servers ---- */

async function renderMCPRows(servers) {
  const rows = $("#mcp-rows");
  if (!servers) { rows.textContent = ""; return; } // no manager / fetch failed
  rows.textContent = "";
  if (!servers.length) {
    rows.innerHTML = '<div class="mcp-empty">no servers connected — add one above</div>';
    return;
  }
  for (const s of servers) rows.append(mcpRow(s));
}

function mcpRow(s) {
  const row = document.createElement("div");
  row.className = "mcp-row" + (s.enabled === false ? " off" : "");

  const dot = document.createElement("span");
  dot.className = "dot" + (s.healthy ? "" : " dead");
  dot.title = s.healthy ? "connected" : "not responding";
  row.append(dot);

  const name = document.createElement("div");
  name.innerHTML = `<span class="t-name">${esc(s.name)}</span>`
    + `<span class="t-badge">${esc(s.transport)}</span>`
    + (s.remembered ? '<span class="t-ro">saved</span>' : "");
  name.title = s.target;
  row.append(name);

  const count = document.createElement("div");
  count.className = "t-desc";
  count.textContent = `${s.tools} tool(s)` +
    (s.disabled ? `, ${s.disabled} off` : "") + (s.healthy ? "" : " — unreachable");
  row.append(count);

  const sw = document.createElement("label");
  sw.className = "t-switch";
  const box = document.createElement("input");
  box.type = "checkbox";
  // on while ANY of the server's tools is active; flipping off pulls all
  box.checked = s.disabled < s.tools;
  box.title = "soft-switch this server's whole toolset";
  box.onchange = async () => {
    const ok = await post("/api/mcp/toggle", { name: s.name, enabled: box.checked });
    if (!ok) { box.checked = !box.checked; return; } // refused; revert
    applyHeader(ok);
    refreshMCPRows();
    toast(box.checked ? `${s.name} back on` :
      `${s.name} off — its tools now fail as data until re-enabled`);
  };
  const knob = document.createElement("span");
  knob.className = "knob";
  sw.append(box, knob);
  row.append(sw);

  if (s.transport === "http") {
    const auth = document.createElement("button");
    auth.className = "m-btn mcp-login";
    auth.innerHTML = icon("key", "ico-sm", 1.7);
    auth.setAttribute("aria-label", `sign in to ${s.name}`);
    auth.title = "sign in to this server (OAuth) — also how you refresh "
      + "an expired login";
    auth.onclick = () => mcpLogin(s.name, s.target, auth);
    row.append(auth);
  }

  const rm = document.createElement("button");
  rm.className = "m-btn danger mcp-remove";
  rm.innerHTML = icon("x", "ico-sm", 2);
  rm.title = "disconnect this server and remove its tools";
  rm.setAttribute("aria-label", `disconnect ${s.name}`);
  rm.onclick = async () => {
    if (rm.dataset.armed !== "1") {          // two-step confirm: no native dialogs
      rm.dataset.armed = "1";
      rm.textContent = "sure?";
      setTimeout(() => {
        rm.dataset.armed = "";
        rm.innerHTML = icon("x", "ico-sm", 2);
      }, 2500);
      return;
    }
    const out = await post("/api/mcp/remove", { name: s.name });
    if (!out) return;
    applyHeader(out);
    toast(`removed ${s.name} — ${out.removed} tool(s) gone`);
    refreshMCPRows();
  };
  row.append(rm);
  return row;
}

async function refreshMCPRows() {
  try {
    const res = await fetch("/api/mcp");
    if (res.ok) await renderMCPRows((await res.json()).servers);
  } catch { /* panel may be closing anyway */ }
}

/* the add form: fields mode by default, paste-json for power users */

let mcpMode = "fields";
$("#mcp-add-btn").onclick = () => {
  $("#mcp-add-form").classList.remove("hidden");
  $("#mcp-add-btn").classList.add("hidden");
  $("#mcp-error").classList.add("hidden");
  $("#mcp-add-form").scrollIntoView({ block: "nearest" });
  $("#mcp-name").focus();
};
$("#mcp-cancel-add").onclick = collapseAddForm;
document.querySelectorAll(".m-tab").forEach((tab) => {
  tab.onclick = () => {
    mcpMode = tab.dataset.mode;
    document.querySelectorAll(".m-tab").forEach((t) =>
      t.classList.toggle("active", t === tab));
    $("#mcp-fields").classList.toggle("hidden", mcpMode !== "fields");
    $("#mcp-json").classList.toggle("hidden", mcpMode !== "json");
  };
});
document.querySelectorAll('input[name="mcp-transport"]').forEach((radio) => {
  radio.onchange = () => {
    const http = document.querySelector('input[name="mcp-transport"]:checked').value === "http";
    $("#mcp-command").classList.toggle("hidden", http);
    $("#mcp-url").classList.toggle("hidden", !http);
    // credentials follow the transport: env reaches a child process,
    // headers reach a URL. Showing both would invite filling the wrong one.
    $("#mcp-env").classList.toggle("hidden", http);
    $("#mcp-headers").classList.toggle("hidden", !http);
  };
});

function collapseAddForm() {
  $("#mcp-add-form").classList.add("hidden");
  $("#mcp-add-btn").classList.remove("hidden");
}

// whitespace split that respects "quoted arguments" -- good enough for
// commands with spaces in paths; exotic shells should use the json mode
function splitCommand(text) {
  const out = []; let cur = "", q = null;
  for (const ch of text.trim()) {
    if (q) { if (ch === q) q = null; else cur += ch; }
    else if (ch === '"' || ch === "'") q = ch;
    else if (/\s/.test(ch)) { if (cur) { out.push(cur); cur = ""; } }
    else cur += ch;
  }
  if (cur) out.push(cur);
  return out;
}

$("#mcp-submit").onclick = async () => {
  const err = $("#mcp-error");
  err.classList.add("hidden");
  const remember = $("#mcp-remember").checked;
  const body = mcpMode === "json"
    ? { config: $("#mcp-json-text").value, remember }
    : (() => {
        const transport = document.querySelector(
          'input[name="mcp-transport"]:checked').value;
        const b = { name: $("#mcp-name").value, remember };
        if (transport === "http") {
          b.url = $("#mcp-url").value.trim();
          const headers = {};
          for (const line of $("#mcp-headers").value.split("\n")) {
            const i = line.indexOf(":");
            if (i > 0 && line.slice(0, i).trim())
              headers[line.slice(0, i).trim()] = line.slice(i + 1).trim();
          }
          if (Object.keys(headers).length) b.headers = headers;
        } else {
          const parts = splitCommand($("#mcp-command").value);
          if (!parts.length) {
            err.textContent = "enter the command to run";
            err.classList.remove("hidden");
            return null;
          }
          b.command = parts[0];
          if (parts.length > 1) b.args = parts.slice(1);
        }
        const env = {};
        for (const line of $("#mcp-env").value.split("\n")) {
          const i = line.indexOf("=");
          if (i > 0 && line.slice(0, i).trim()) env[line.slice(0, i).trim()] = line.slice(i + 1);
        }
        if (transport !== "http" && Object.keys(env).length) b.env = env;
        return b;
      })();
  if (body === null) return;

  const out = await post("/api/mcp/add", body);
  if (!out) return; // validation failure -- post() already toasted the detail
  const failed = (out.results || []).filter((r) => !r.ok);
  const added = (out.results || []).filter((r) => r.ok);
  applyHeader(out);
  if (added.length) {
    toast(added.map((r) => `${r.name}: ${r.tools} tool(s)`).join(", ")
      + ` connected${remember ? " · saved" : ""}`);
    collapseAddForm();
    $("#mcp-name").value = $("#mcp-command").value =
      $("#mcp-url").value = $("#mcp-env").value = $("#mcp-json-text").value = "";
  }
  if (failed.length) {
    err.textContent = failed.map((r) => `${r.name}: ${r.error}`).join("\n");
    err.classList.remove("hidden");
    // A 401 is the one failure with an obvious next move, so offer it
    // here rather than making the reader work out that "needs
    // authentication" means "there is a button for this".
    const needsAuth = failed.find((r) =>
      /needs authentication|HTTP 401/i.test(r.error || ""));
    if (needsAuth && body.url) {
      const go = document.createElement("button");
      go.className = "m-btn primary";
      go.style.marginTop = ".5rem";
      go.textContent = `sign in to ${needsAuth.name}`;
      go.onclick = () => mcpLogin(needsAuth.name, body.url, go);
      err.append(document.createElement("br"), go);
    }
  }
  refreshMCPRows();
};

// Shared by the add-form's 401 and a row's padlock: the browser opens on
// the machine running the server, so the button goes quiet for as long as
// that takes and reports whichever way it lands.
async function mcpLogin(name, url, button) {
  const label = button.innerHTML;
  button.disabled = true;
  button.textContent = "waiting for the browser…";
  try {
    const out = await post("/api/mcp/login", url ? { name, url } : { name });
    if (!out) return;
    if (out.ok) {
      toast(out.already
        ? `${name} needed no login`
        : `${name} signed in — ${out.tools} tool(s)`);
      applyHeader(out);
      collapseAddForm();
      refreshMCPRows();
      return;
    }
    toast(`${name}: ${out.error}`);
    if (out.authorize_url) window.open(out.authorize_url, "_blank");
  } finally {
    button.disabled = false;
    button.innerHTML = label;
  }
}

function toolRow(t) {
  const row = document.createElement("div");
  row.className = "tool-row" + (t.enabled ? "" : " off");

  const name = document.createElement("div");
  name.innerHTML = `<span class="t-name">${esc(t.name)}</span>`
    + (t.read_only ? '<span class="t-ro">read-only</span>' : "");
  row.append(name);

  const desc = document.createElement("div");
  desc.className = "t-desc";
  desc.textContent = t.description;
  row.append(desc);

  const sw = document.createElement("label");
  sw.className = "t-switch";
  const box = document.createElement("input");
  box.type = "checkbox";
  box.checked = t.enabled;
  box.title = t.enabled ? "disable this tool" : "re-enable this tool";
  box.onchange = async () => {
    const s = await post("/api/tools", { name: t.name, enabled: box.checked });
    if (!s) { box.checked = !box.checked; return; } // server refused; revert
    applyHeader(s);
    row.classList.toggle("off", !box.checked);
    toast(`${t.name} ${box.checked ? "enabled" : "disabled — its calls now "
      + "fail as data until re-enabled"}`);
  };
  const knob = document.createElement("span");
  knob.className = "knob";
  sw.append(box, knob);
  row.append(sw);
  return row;
}

function closeToolsPanel() {
  $("#tools-backdrop").classList.add("hidden");
}

let toastTimer = null;
function toast(msg) {
  // rebuilt rather than reused, so a second toast replays its entrance
  $("#toast")?.remove();
  const el = document.createElement("div");
  el.id = "toast";
  el.setAttribute("role", "status");
  el.textContent = msg;
  document.body.append(el);
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.remove(), 3400);
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

/* The welcome surface is a sibling of the transcript (which gets wiped
   wholesale on reconnect, load and clear), so one observer keeps it honest
   rather than a call at every append site. */
const emptyState = $("#empty-state");
function syncEmptyState() {
  emptyState.classList.toggle("hidden", transcript.childElementCount > 0);
}
new MutationObserver(syncEmptyState).observe(transcript, { childList: true });
syncEmptyState();

connect();
