"""Local chat simulator for the guided flow — try the bot without Telegram.

    .venv/bin/python -m pizzabot.simulator          # http://127.0.0.1:8765

Standard library only. One in-memory conversation with the demo restaurant
(``pizzabot.flow.demo``); confirmed orders show up in a "kitchen" panel.
Old buttons stay clickable, like on Telegram, so stale-button handling can
be tried too. Local development tool — binds to 127.0.0.1 only.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock

from pizzabot.flow import Choice, Conversation, FlowContext, Screen, Text, handle, render
from pizzabot.flow.demo import demo_menu, demo_settings

MENU = demo_menu()
SETTINGS = demo_settings()

_lock = Lock()
_conv: Conversation
_orders: list[dict] = []
_booked: dict[datetime, int] = {}


def _new_conversation() -> Conversation:
    return Conversation(restaurant_id=SETTINGS.restaurant_id, channel="telegram",
                        customer_ref="simulator")


def _ctx() -> FlowContext:
    return FlowContext(SETTINGS, MENU, datetime.now(timezone.utc), dict(_booked))


def _screen_json(screen: Screen) -> dict:
    return {
        "text": screen.text,
        "options": [{"id": o.id, "label": o.label} for o in screen.options],
        "accepts_text": screen.accepts_text,
    }


def _state() -> dict:
    return {"screen": _screen_json(render(_conv, _ctx())), "orders": _orders,
            "state": _conv.state.value}


def _apply(event) -> dict:
    global _conv
    result = handle(_conv, event, _ctx())
    _conv = result.conversation
    for effect in result.effects:
        order = effect.order
        if order.slot_at is not None:
            _booked[order.slot_at] = _booked.get(order.slot_at, 0) + 1
        _orders.insert(0, order.model_dump_safe())
    return {"screen": _screen_json(result.screen), "orders": _orders,
            "state": _conv.state.value}


_conv = _new_conversation()


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data: dict, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/":
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            with _lock:
                self._json(_state())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        global _conv
        length = int(self.headers.get("Content-Length") or 0)
        if length > 10_000:
            self._json({"error": "too large"}, 413)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._json({"error": "bad json"}, 400)
            return
        with _lock:
            if self.path == "/api/reset":
                _conv = _new_conversation()
                _orders.clear()
                _booked.clear()
                self._json(_state())
            elif self.path == "/api/event":
                value = str(body.get("value", ""))
                event = Choice(value) if body.get("type") == "choice" else Text(value)
                self._json(_apply(event))
            else:
                self._json({"error": "not found"}, 404)

    def log_message(self, fmt: str, *args) -> None:
        pass  # keep the terminal quiet


PAGE = r"""<!doctype html>
<html lang="it">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PizzaBot Simulator</title>
<style>
  :root { --bg:#eef1f4; --panel:#fff; --bot:#fff; --me:#d9fdd3; --ink:#1b1f24; --muted:#6a737d;
          --btn:#f2f6fa; --btn-ink:#1f6feb; --line:#d8dee4; }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#0d1117; --panel:#161b22; --bot:#1f2630; --me:#1e3a2a; --ink:#e6edf3; --muted:#8b949e;
            --btn:#21262d; --btn-ink:#58a6ff; --line:#30363d; }
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--ink);
         font:15px/1.4 system-ui,-apple-system,"Segoe UI",sans-serif; }
  .wrap { display:flex; gap:16px; padding:16px; max-width:1100px; margin:auto; height:100vh; }
  .chat, .kitchen { background:var(--panel); border:1px solid var(--line); border-radius:12px;
                    display:flex; flex-direction:column; min-height:0; }
  .chat { flex:1 1 420px; max-width:480px; }
  .kitchen { flex:1 1 360px; }
  header { padding:12px 16px; border-bottom:1px solid var(--line); display:flex;
           justify-content:space-between; align-items:center; font-weight:600; }
  header small { color:var(--muted); font-weight:400; }
  .log { flex:1; overflow-y:auto; padding:12px; display:flex; flex-direction:column; gap:8px; }
  .msg { max-width:85%; padding:8px 12px; border-radius:10px; white-space:pre-wrap; word-wrap:break-word; }
  .bot { background:var(--bot); border:1px solid var(--line); align-self:flex-start; }
  .me { background:var(--me); align-self:flex-end; }
  .kb { display:flex; flex-wrap:wrap; gap:6px; margin-top:8px; }
  .kb button { flex:1 1 45%; padding:7px 8px; border:1px solid var(--line); border-radius:8px;
               background:var(--btn); color:var(--btn-ink); cursor:pointer; font:inherit; font-size:14px; }
  .kb button:hover { filter:brightness(0.97); }
  form { display:flex; gap:8px; padding:10px; border-top:1px solid var(--line); }
  input { flex:1; padding:9px 12px; border:1px solid var(--line); border-radius:20px;
          background:var(--bg); color:var(--ink); font:inherit; }
  .send, .reset { border:0; border-radius:20px; padding:0 14px; cursor:pointer; font:inherit; }
  .send { background:var(--btn-ink); color:#fff; }
  .reset { background:transparent; color:var(--muted); border:1px solid var(--line); padding:4px 10px; }
  .orders { flex:1; overflow-y:auto; padding:12px; display:flex; flex-direction:column; gap:10px; }
  .order { border:1px solid var(--line); border-radius:10px; padding:10px 12px; }
  .order h3 { margin:0 0 6px; font-size:15px; }
  .order .meta { color:var(--muted); font-size:13px; }
  .empty { color:var(--muted); text-align:center; margin-top:40px; }
  @media (max-width:760px) { .wrap { flex-direction:column; height:auto; } .chat { max-width:none; height:80vh; } }
</style>
</head>
<body>
<div class="wrap">
  <section class="chat">
    <header><span>🍕 Pizzeria Demo <small id="state"></small></span>
      <button class="reset" id="reset" title="Nuova conversazione">↺ Reset</button></header>
    <div class="log" id="log"></div>
    <form id="form"><input id="input" autocomplete="off" placeholder="Scrivi un messaggio…">
      <button class="send">➤</button></form>
  </section>
  <section class="kitchen">
    <header><span>👨‍🍳 Cucina <small>ordini confermati</small></span></header>
    <div class="orders" id="orders"></div>
  </section>
</div>
<script>
const log = document.getElementById("log");
const esc = s => s.replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const fmt = s => esc(s).replace(/\*([^*\n]+)\*/g, "<b>$1</b>");
const eur = v => Number(v).toFixed(2).replace(".", ",") + " €";

function addMsg(html, cls) {
  const div = document.createElement("div");
  div.className = "msg " + cls;
  div.innerHTML = html;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function showScreen(screen) {
  const div = addMsg(fmt(screen.text), "bot");
  if (screen.options.length) {
    const kb = document.createElement("div");
    kb.className = "kb";
    for (const o of screen.options) {
      const b = document.createElement("button");
      b.textContent = o.label;
      b.onclick = () => send("choice", o.id, o.label);
      kb.appendChild(b);
    }
    div.appendChild(kb);
  }
}

function showOrders(orders) {
  const box = document.getElementById("orders");
  if (!orders.length) { box.innerHTML = '<div class="empty">Nessun ordine ancora.</div>'; return; }
  box.innerHTML = orders.map(o => {
    const lines = o.items.map(i => {
      const opts = i.options.map(x => x.name).join(", ");
      return `${i.quantity}× ${esc(i.dish_name)}${opts ? " (" + esc(opts) + ")" : ""} — ${eur(i.subtotal_eur)}`;
    }).join("<br>");
    const when = o.slot_at ? new Date(o.slot_at).toLocaleTimeString("it-IT", {hour:"2-digit", minute:"2-digit"}) : "prima possibile";
    const where = o.fulfilment === "delivery" ? "🛵 " + esc(o.address) : "🏃 Ritiro";
    return `<div class="order"><h3>${eur(o.total_eur)} · ${where}</h3>${lines}
      <div class="meta">🕒 ${when}${o.kitchen_notes ? "<br>📝 " + esc(o.kitchen_notes) : ""}
      <br>IVA ${eur(o.vat_eur)} · imponibile ${eur(o.net_eur)} · id ${esc(o.order_id.slice(0, 8))}</div></div>`;
  }).join("");
}

function apply(data) {
  document.getElementById("state").textContent = "· " + data.state;
  showScreen(data.screen);
  showOrders(data.orders);
}

async function send(type, value, shown) {
  addMsg(esc(shown ?? value), "me");
  const r = await fetch("/api/event", {method:"POST", headers:{"Content-Type":"application/json"},
                                       body: JSON.stringify({type, value})});
  apply(await r.json());
}

document.getElementById("form").onsubmit = e => {
  e.preventDefault();
  const input = document.getElementById("input");
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  send("text", text);
};

document.getElementById("reset").onclick = async () => {
  log.innerHTML = "";
  apply(await (await fetch("/api/reset", {method:"POST"})).json());
};

fetch("/api/state").then(r => r.json()).then(apply);
</script>
</body>
</html>
"""


def main() -> None:
    port = int(os.environ.get("PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    print(f"PizzaBot simulator → http://127.0.0.1:{port}  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
