"""Guided ordering flow — the channel-agnostic state machine (decision 1).

    handle(conversation, event, ctx) -> Result(conversation, screen, effects)

Pure: no I/O, no clock (``ctx.now``), no randomness. The caller loads the
conversation, calls ``handle``, saves the returned conversation, renders the
screen on its channel and applies the effects (e.g. persist the order).

Rules that hold in every state:
- Only the ids of the *current* screen's options are accepted; anything else
  (stale buttons from old messages) is answered with a notice + the screen.
- Free text is accepted only where the screen says ``accepts_text``
  (address, kitchen notes). Elsewhere → "Usa i pulsanti" + the screen.
- Money is never taken from the conversation: the cart stores ids, and every
  price shown or confirmed is recomputed from the menu.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Mapping
import re

from pydantic import BaseModel, ConfigDict, Field

from pizzabot.flow.menu import ALLERGENS_IT, Menu, MenuItem, RestaurantSettings
from pizzabot.flow import slots as slot_rules
from pizzabot.order.schema import (
    MAX_KITCHEN_NOTES,
    CartItem,
    CartOption,
    Channel,
    Fulfilment,
    Order,
    clean_kitchen_notes,
    round_eur,
)


MAX_LINE_QUANTITY = 99
MAX_ADDRESS_LEN = 200
_CAP_RE = re.compile(r"\b(\d{5})\b")


# --- conversation (persisted as JSON) ----------------------------------------

class State(str, Enum):
    start = "start"
    closed = "closed"
    category = "category"
    item = "item"
    item_detail = "item_detail"
    option_group = "option_group"
    quantity = "quantity"
    cart_review = "cart_review"
    edit_line = "edit_line"
    fulfilment = "fulfilment"
    address = "address"
    slot = "slot"
    notes = "notes"
    confirm = "confirm"
    done = "done"


class CartLine(BaseModel):
    """A cart line holds ids only — never prices."""

    model_config = ConfigDict(extra="forbid")

    menu_item_id: str
    option_ids: list[str] = Field(default_factory=list)
    quantity: int = Field(..., ge=1, le=MAX_LINE_QUANTITY)


class Conversation(BaseModel):
    """One customer's conversation with one restaurant on one channel."""

    model_config = ConfigDict(extra="forbid")

    restaurant_id: str
    channel: Channel
    customer_ref: str
    state: State = State.start
    # In-progress choices: category_id, item_id, group_index, selected,
    # line_index, fulfilment, address, slot, notes.
    data: dict = Field(default_factory=dict)
    cart: list[CartLine] = Field(default_factory=list)


# --- events, screens, effects -------------------------------------------------

@dataclass(frozen=True)
class Choice:
    """The customer pressed a button / picked a list row."""
    id: str


@dataclass(frozen=True)
class Text:
    """The customer typed a message."""
    text: str


Event = Choice | Text


@dataclass(frozen=True)
class ScreenOption:
    id: str      # short, ASCII — fits Telegram callback_data (64 bytes)
    label: str


@dataclass(frozen=True)
class Screen:
    """What to show. Adapters decide buttons vs lists and paginate."""
    text: str
    options: tuple[ScreenOption, ...] = ()
    accepts_text: bool = False

    def option_ids(self) -> set[str]:
        return {o.id for o in self.options}


@dataclass(frozen=True)
class PlaceOrder:
    """Effect: persist this order and notify the kitchen."""
    order: Order


@dataclass(frozen=True)
class FlowContext:
    settings: RestaurantSettings
    menu: Menu
    now: datetime
    # Orders already booked per slot (restaurant timezone), from storage.
    booked_slots: Mapping[datetime, int] = field(default_factory=dict)


@dataclass(frozen=True)
class Result:
    conversation: Conversation
    screen: Screen
    effects: tuple[PlaceOrder, ...] = ()


# --- Italian copy --------------------------------------------------------------

BACK = ScreenOption("back", "⬅ Indietro")
CANCEL = ScreenOption("cancel", "✖ Annulla")

MSG_USE_BUTTONS = "Usa i pulsanti qui sotto 👇"
MSG_STALE = "Questa scelta non è più valida."
MSG_CANCELLED = "Ordine annullato."


def eur(amount: Decimal) -> str:
    """Italian price format: 1234.5 → "1234,50 €"."""
    return f"{round_eur(amount):.2f}".replace(".", ",") + " €"


def _delta(amount: Decimal) -> str:
    if amount > 0:
        return f" (+{eur(amount)})"
    if amount < 0:
        return f" (−{eur(-amount)})"
    return ""


# --- pricing helpers -------------------------------------------------------------

@dataclass(frozen=True)
class PricedLine:
    index: int
    line: CartLine
    item: MenuItem | None
    unit_price: Decimal
    available: bool

    @property
    def subtotal(self) -> Decimal:
        return round_eur(self.unit_price * self.line.quantity)


def _line_available(menu: Menu, line: CartLine) -> bool:
    item = menu.item(line.menu_item_id)
    if item is None or not item.available:
        return False
    for oid in line.option_ids:
        found = menu.option(oid)
        if found is None or not found[1].available or found[0].id not in item.option_group_ids:
            return False
    return True


def _price_cart(menu: Menu, cart: list[CartLine]) -> list[PricedLine]:
    priced = []
    for i, line in enumerate(cart):
        ok = _line_available(menu, line)
        item = menu.item(line.menu_item_id)
        price = menu.unit_price(item, line.option_ids) if ok else Decimal("0.00")
        priced.append(PricedLine(i, line, item, price, ok))
    return priced


def _items_total(priced: list[PricedLine]) -> Decimal:
    return round_eur(sum((p.subtotal for p in priced if p.available), Decimal(0)))


def _is_standard_option(menu: Menu, option_id: str) -> bool:
    """The first option of a required group (Normale, Classico) is the
    standard one. Customers don't need to read it on every line; the order
    snapshot sent to the kitchen still lists every option."""
    found = menu.option(option_id)
    if found is None:
        return False
    group, option = found
    return group.min_select > 0 and group.options[0].id == option.id


def _line_label(menu: Menu, line: CartLine) -> str:
    """Customer-facing line text: only what differs from the standard pizza."""
    item = menu.item(line.menu_item_id)
    name = item.name if item else "?"
    opts = [menu.option(o)[1].name for o in line.option_ids
            if menu.option(o) and not _is_standard_option(menu, o)]
    return f"{line.quantity}× {name}" + (f" ({', '.join(opts)})" if opts else "")


def _drop_unavailable(conv: Conversation, menu: Menu) -> str | None:
    """Remove lines that can no longer be ordered. Returns a notice or None."""
    gone = [l for l in conv.cart if not _line_available(menu, l)]
    if not gone:
        return None
    conv.cart = [l for l in conv.cart if _line_available(menu, l)]
    names = ", ".join(_line_label(menu, l) for l in gone)
    return f"⚠ Non più disponibile, rimosso dal carrello: {names}"


# --- rendering -------------------------------------------------------------------

def render(conv: Conversation, ctx: FlowContext) -> Screen:
    s, d, menu, settings = conv.state, conv.data, ctx.menu, ctx.settings

    if s is State.start:
        return Screen(
            f"Benvenuto da {settings.name}! 🍕\nOrdina in pochi passi.",
            (ScreenOption("order", "🍕 Ordina"),),
        )

    if s is State.closed:
        return Screen(
            f"{settings.name} al momento non accetta ordini per oggi.\n\n"
            f"Orari:\n{slot_rules.hours_text(settings)}",
            (ScreenOption("order", "🔄 Riprova"),),
        )

    if s is State.done:
        return Screen(
            "Ordine inviato! ✅\nTi avvisiamo quando è pronto.",
            (ScreenOption("order", "🍕 Nuovo ordine"),),
        )

    if s is State.category:
        opts = [ScreenOption(f"cat:{c.id}", c.name) for c in menu.active_categories()]
        opts += _cart_button(conv, menu)
        return _with_nav(Screen("Cosa desideri?", tuple(opts)))

    if s is State.item:
        cat = menu.category(d["category_id"])
        opts = [
            ScreenOption(f"item:{i.id}", f"{i.name} — {eur(i.base_price_eur)}")
            for i in menu.available_items(d["category_id"])
        ]
        opts += _cart_button(conv, menu)
        # "Back" here means "the other categories" — say so, so moving on to
        # drinks after the pizzas is obvious.
        opts += [ScreenOption("menu", "📂 Altre categorie"), CANCEL]
        return Screen(cat.name if cat else "Menu", tuple(opts))

    if s is State.item_detail:
        item = menu.item(d["item_id"])
        lines = [f"*{item.name}* — {eur(item.base_price_eur)}"]
        if item.description:
            lines.append(item.description)
        lines.append(_allergen_line(menu.allergens_for(item)))
        if item.age_restricted:
            lines.append("🔞 Vendita vietata ai minori di 18 anni.")
        opts = [ScreenOption("add", "➕ Aggiungi"), ScreenOption("add:2", "×2"), ScreenOption("add:3", "×3")]
        if menu.groups_for(item):
            defaults = [menu.option(o)[1].name for o in _default_option_ids(menu, item)]
            if defaults:
                lines.append(f"Standard: {', '.join(defaults)}")
            opts.append(ScreenOption("custom", "⚙️ Personalizza"))
        return _with_nav(Screen("\n".join(lines), tuple(opts)))

    if s is State.option_group:
        item = menu.item(d["item_id"])
        group = menu.groups_for(item)[d["group_index"]]
        chosen = set(d.get("selected", {}).get(group.id, []))
        opts = []
        for o in group.available_options():
            mark = "✓ " if o.id in chosen else ""
            opts.append(ScreenOption(f"opt:{o.id}", f"{mark}{o.name}{_delta(o.price_delta_eur)}"))
        if not group.is_single or group.min_select == 0:
            opts.append(ScreenOption("next", "Avanti ▶"))
        hint = ""
        if not group.is_single:
            hint = f"\nScegli fino a {group.max_select}, poi premi Avanti."
        return _with_nav(Screen(f"{item.name}: {group.name}{hint}", tuple(opts)))

    if s is State.quantity:
        item = menu.item(d["item_id"])
        option_ids = _selected_option_ids(conv, menu)
        label = _line_label(menu, CartLine(menu_item_id=item.id, option_ids=option_ids, quantity=1))[3:]
        price = menu.unit_price(item, option_ids)
        opts = tuple(ScreenOption(f"qty:{n}", str(n)) for n in range(1, 10))
        return _with_nav(Screen(f"{label} — {eur(price)}\nQuante?", opts))

    if s is State.cart_review:
        return _render_cart(conv, ctx)

    if s is State.edit_line:
        idx = d.get("line_index")
        if idx is None:
            opts = tuple(ScreenOption(f"line:{i}", _line_label(menu, l)) for i, l in enumerate(conv.cart))
            return _with_nav(Screen("Quale vuoi modificare?", opts))
        line = conv.cart[idx]
        opts = [ScreenOption("dec", "➖ 1"), ScreenOption("inc", "➕ 1"), ScreenOption("rm", "🗑 Rimuovi")]
        return _with_nav(Screen(_line_label(menu, line), tuple(opts)))

    if s is State.fulfilment:
        opts = []
        if settings.pickup_enabled:
            opts.append(ScreenOption("pickup", "🏃 Ritiro"))
        if settings.delivery_enabled:
            fee = f" (+{eur(settings.delivery_fee_eur)})" if settings.delivery_fee_eur else ""
            opts.append(ScreenOption("delivery", f"🛵 Consegna{fee}"))
        return _with_nav(Screen("Ritiro o consegna?", tuple(opts)))

    if s is State.address:
        hint = " (con il CAP)" if settings.delivery_postcodes else ""
        return _with_nav(Screen(f"Scrivi l'indirizzo di consegna{hint}.", accepts_text=True))

    if s is State.slot:
        opts = []
        if slot_rules.is_open(settings, ctx.now):
            opts.append(ScreenOption("slot:asap", "⚡ Prima possibile"))
        for t in slot_rules.available_slots(settings, ctx.now, ctx.booked_slots):
            opts.append(ScreenOption(f"slot:{t:%H%M}", f"{t:%H:%M}"))
        word = "la consegna" if d.get("fulfilment") == Fulfilment.delivery.value else "il ritiro"
        text = f"A che ora per {word}?" if opts else "Nessun orario disponibile oggi."
        return _with_nav(Screen(text, tuple(opts)))

    if s is State.notes:
        return _with_nav(Screen(
            f"Note per la cucina? (es. \"senza cipolla\")\nMassimo {MAX_KITCHEN_NOTES} caratteri.",
            (ScreenOption("skip", "Salta ▶"),),
            accepts_text=True,
        ))

    if s is State.confirm:
        return _render_confirm(conv, ctx)

    raise AssertionError(f"unhandled state {s}")


def _cart_button(conv: Conversation, menu: Menu) -> list[ScreenOption]:
    if not conv.cart:
        return []
    count = sum(l.quantity for l in conv.cart)
    total = _items_total(_price_cart(menu, conv.cart))
    return [ScreenOption("cart", f"🛒 Carrello ({count}) — {eur(total)}")]


def _with_nav(screen: Screen) -> Screen:
    return Screen(screen.text, screen.options + (BACK, CANCEL), screen.accepts_text)


def _allergen_line(codes: list[int]) -> str:
    if not codes:
        return "Allergeni: nessuno dichiarato."
    return "Allergeni: " + ", ".join(ALLERGENS_IT[c] for c in codes if c in ALLERGENS_IT)


def _render_cart(conv: Conversation, ctx: FlowContext) -> Screen:
    menu, settings = ctx.menu, ctx.settings
    if not conv.cart:
        return _with_nav(Screen("Il carrello è vuoto.", (ScreenOption("more", "➕ Aggiungi"),)))
    priced = _price_cart(menu, conv.cart)
    lines = [f"{_line_label(menu, p.line)} — {eur(p.subtotal)}" for p in priced]
    total = _items_total(priced)
    lines.append(f"\nTotale: {eur(total)}")
    opts = []
    if total >= settings.min_order_eur:
        opts.append(ScreenOption("proceed", "✅ Procedi"))
    else:
        lines.append(f"Ordine minimo: {eur(settings.min_order_eur)}")
    opts += [ScreenOption("edit", "✏️ Modifica"), ScreenOption("more", "➕ Aggiungi altro")]
    return _with_nav(Screen("🛒 Il tuo ordine:\n" + "\n".join(lines), tuple(opts)))


def _render_confirm(conv: Conversation, ctx: FlowContext) -> Screen:
    order = _build_order(conv, ctx)
    menu = ctx.menu
    lines = ["Riepilogo:"]
    lines += [f"{_line_label(menu, l)} — {eur(p.subtotal)}"
              for l, p in zip(conv.cart, _price_cart(menu, conv.cart))]
    if order.fulfilment is Fulfilment.delivery:
        if order.delivery_fee_eur:
            lines.append(f"Consegna — {eur(order.delivery_fee_eur)}")
        lines.append(f"\n🛵 Consegna a: {order.address}")
    else:
        lines.append("\n🏃 Ritiro al locale")
    when = "prima possibile" if order.slot_at is None else f"{order.slot_at:%H:%M}"
    lines.append(f"🕒 {when}")
    if order.kitchen_notes:
        lines.append(f"📝 {order.kitchen_notes}")
    lines.append(f"\nTotale: {eur(order.total_eur)} (di cui IVA {eur(order.vat_eur)})")
    lines.append("Pagamento alla consegna / al ritiro.")
    return _with_nav(Screen("\n".join(lines), (
        ScreenOption("confirm", "✅ Conferma"), ScreenOption("edit", "✏️ Modifica"),
    )))


# --- transitions -------------------------------------------------------------------

def _selected_option_ids(conv: Conversation, menu: Menu) -> list[str]:
    """Chosen options in group order (stable, so equal lines merge)."""
    item = menu.item(conv.data["item_id"])
    selected = conv.data.get("selected", {})
    return [oid for g in menu.groups_for(item) for oid in selected.get(g.id, [])]


def _go(conv: Conversation, state: State, **data) -> None:
    conv.state = state
    conv.data.update(data)


def _reset(conv: Conversation, state: State) -> None:
    """Back to a state with a clean in-progress line (cart and checkout data kept)."""
    for k in ("category_id", "item_id", "group_index", "selected", "line_index"):
        conv.data.pop(k, None)
    conv.state = state


def _start_order(conv: Conversation, ctx: FlowContext) -> None:
    if slot_rules.can_order(ctx.settings, ctx.now, ctx.booked_slots):
        _reset(conv, State.category)
    else:
        _reset(conv, State.closed)


def _cancel(conv: Conversation) -> None:
    conv.cart = []
    conv.data = {}
    conv.state = State.start


def _back(conv: Conversation, ctx: FlowContext) -> None:
    s, d, menu = conv.state, conv.data, ctx.menu
    if s is State.category:
        _reset(conv, State.cart_review if conv.cart else State.start)
    elif s is State.item:
        _reset(conv, State.category)
    elif s is State.item_detail:
        conv.state = State.item
        d.pop("item_id", None)
        d.pop("selected", None)
    elif s is State.option_group:
        if d["group_index"] > 0:
            d["group_index"] -= 1
        else:
            conv.state = State.item_detail
            d.pop("group_index", None)
    elif s is State.quantity:
        groups = menu.groups_for(menu.item(d["item_id"]))
        if groups:
            _go(conv, State.option_group, group_index=len(groups) - 1)
        else:
            conv.state = State.item_detail
    elif s is State.edit_line:
        if d.get("line_index") is not None:
            d.pop("line_index")
        else:
            conv.state = State.cart_review
    elif s is State.cart_review:
        _reset(conv, State.category)
    elif s is State.fulfilment:
        conv.state = State.cart_review
    elif s is State.address:
        conv.state = _fulfilment_entry(ctx, back=True)
    elif s is State.slot:
        if d.get("fulfilment") == Fulfilment.delivery.value:
            conv.state = State.address
        else:
            conv.state = _fulfilment_entry(ctx, back=True)
    elif s is State.notes:
        conv.state = State.slot
    elif s is State.confirm:
        conv.state = State.notes


def _fulfilment_entry(ctx: FlowContext, *, back: bool = False) -> State:
    """With only one mode enabled the fulfilment question is skipped."""
    settings = ctx.settings
    if settings.pickup_enabled and settings.delivery_enabled:
        return State.fulfilment
    return State.cart_review if back else State.fulfilment


def _choose_fulfilment(conv: Conversation, mode: Fulfilment) -> None:
    conv.data["fulfilment"] = mode.value
    if mode is Fulfilment.delivery:
        conv.state = State.address
    else:
        conv.data.pop("address", None)
        conv.state = State.slot


def _proceed_to_checkout(conv: Conversation, ctx: FlowContext) -> None:
    s = ctx.settings
    if s.pickup_enabled and s.delivery_enabled:
        conv.state = State.fulfilment
    else:
        _choose_fulfilment(conv, Fulfilment.pickup if s.pickup_enabled else Fulfilment.delivery)


def _default_option_ids(menu: Menu, item: MenuItem) -> list[str]:
    """The standard pizza: for each required group, its first available
    option(s); optional groups (extras) stay empty. Operators control the
    default by ordering options."""
    ids = []
    for g in menu.groups_for(item):
        ids += [o.id for o in g.available_options()[: g.min_select]]
    return ids


def _add_line(conv: Conversation, ctx: FlowContext, quantity: int,
              option_ids: list[str]) -> str:
    """Add (or merge) a line, then go back to the same category's items so
    the next product is two taps away. Returns the confirmation notice."""
    item_id = conv.data["item_id"]
    for line in conv.cart:
        if line.menu_item_id == item_id and line.option_ids == option_ids:
            line.quantity = min(MAX_LINE_QUANTITY, line.quantity + quantity)
            break
    else:
        conv.cart.append(CartLine(menu_item_id=item_id, option_ids=option_ids, quantity=quantity))
    label = _line_label(ctx.menu, CartLine(menu_item_id=item_id, option_ids=option_ids, quantity=quantity))
    category_id = conv.data.get("category_id")
    _reset(conv, State.item)
    conv.data["category_id"] = category_id
    return f"✓ Aggiunto: {label}"


def _after_options(conv: Conversation, ctx: FlowContext) -> None:
    """Advance to the next option group, or to quantity after the last."""
    groups = ctx.menu.groups_for(ctx.menu.item(conv.data["item_id"]))
    nxt = conv.data.get("group_index", -1) + 1
    if nxt < len(groups):
        _go(conv, State.option_group, group_index=nxt)
    else:
        conv.state = State.quantity


def _handle_option(conv: Conversation, ctx: FlowContext, cid: str) -> str | None:
    menu = ctx.menu
    group = menu.groups_for(menu.item(conv.data["item_id"]))[conv.data["group_index"]]
    selected = conv.data.setdefault("selected", {})
    chosen = list(selected.get(group.id, []))

    if cid == "next":
        if len(chosen) < group.min_select:
            return f"Scegli almeno {group.min_select} opzione."
        _after_options(conv, ctx)
        return None

    oid = cid.removeprefix("opt:")
    if group.is_single:
        selected[group.id] = [oid]
        _after_options(conv, ctx)
        return None
    if oid in chosen:
        chosen.remove(oid)
    elif len(chosen) >= group.max_select:
        return f"Puoi sceglierne al massimo {group.max_select}."
    else:
        chosen.append(oid)
    selected[group.id] = chosen
    return None


def _handle_edit(conv: Conversation, cid: str) -> None:
    if cid.startswith("line:"):
        conv.data["line_index"] = int(cid.removeprefix("line:"))
        return
    idx = conv.data.pop("line_index")
    line = conv.cart[idx]
    if cid == "inc":
        line.quantity = min(MAX_LINE_QUANTITY, line.quantity + 1)
    elif cid == "dec" and line.quantity > 1:
        line.quantity -= 1
    else:  # "rm", or "dec" at 1
        conv.cart.pop(idx)
    conv.state = State.cart_review


def _handle_address(conv: Conversation, ctx: FlowContext, text: str) -> str | None:
    address = " ".join(text.split())
    if len(address) < 5:
        return "Indirizzo troppo corto. Scrivi via, numero civico e città."
    if len(address) > MAX_ADDRESS_LEN:
        return f"Indirizzo troppo lungo (massimo {MAX_ADDRESS_LEN} caratteri)."
    allowed = ctx.settings.delivery_postcodes
    if allowed:
        caps = _CAP_RE.findall(address)
        if not caps:
            return "Aggiungi il CAP all'indirizzo."
        if not any(c in allowed for c in caps):
            return "Ci dispiace, non consegniamo in questa zona."
    _go(conv, State.slot, address=address)
    return None


def _resolve_slot(conv: Conversation, ctx: FlowContext) -> datetime | None:
    hhmm = conv.data.get("slot")
    if hhmm in (None, "asap"):
        return None
    for t in slot_rules.available_slots(ctx.settings, ctx.now, ctx.booked_slots, limit=10_000):
        if f"{t:%H%M}" == hhmm:
            return t
    raise LookupError(hhmm)


def _build_order(conv: Conversation, ctx: FlowContext) -> Order:
    menu, settings = ctx.menu, ctx.settings
    items = []
    for line in conv.cart:
        item = menu.item(line.menu_item_id)
        opts = [menu.option(o)[1] for o in line.option_ids]
        items.append(CartItem(
            menu_item_id=item.id,
            dish_name=item.name,
            options=[CartOption(option_id=o.id, name=o.name, price_delta_eur=o.price_delta_eur) for o in opts],
            quantity=line.quantity,
            unit_price_eur=menu.unit_price(item, line.option_ids),
        ))
    delivery = conv.data.get("fulfilment") == Fulfilment.delivery.value
    return Order(
        restaurant_id=conv.restaurant_id,
        channel=conv.channel,
        customer_ref=conv.customer_ref,
        items=items,
        fulfilment=Fulfilment.delivery if delivery else Fulfilment.pickup,
        address=conv.data.get("address") if delivery else None,
        slot_at=_resolve_slot(conv, ctx),
        kitchen_notes=conv.data.get("notes"),
        delivery_fee_eur=settings.delivery_fee_eur if delivery else Decimal("0"),
        vat_rate=settings.vat_rate,
        created_at=ctx.now,
    )


def _checkout_problem(conv: Conversation, ctx: FlowContext) -> tuple[State, str] | None:
    """Re-validate everything before confirm/confirmation: availability,
    minimum order, slot still free. Returns (state to go back to, notice)."""
    notice = _drop_unavailable(conv, ctx.menu)
    if notice or not conv.cart:
        return State.cart_review, notice or "Il carrello è vuoto."
    if _items_total(_price_cart(ctx.menu, conv.cart)) < ctx.settings.min_order_eur:
        return State.cart_review, f"Ordine minimo: {eur(ctx.settings.min_order_eur)}"
    try:
        _resolve_slot(conv, ctx)
    except LookupError:
        conv.data.pop("slot", None)
        return State.slot, "L'orario scelto non è più disponibile, scegline un altro."
    if conv.data.get("slot") == "asap" and not slot_rules.is_open(ctx.settings, ctx.now):
        conv.data.pop("slot", None)
        return State.slot, "Siamo chiusi in questo momento, scegli un orario."
    return None


def _transition(conv: Conversation, cid: str, ctx: FlowContext) -> tuple[str | None, tuple[PlaceOrder, ...]]:
    """Apply a valid choice. Returns (notice, effects)."""
    s, menu = conv.state, ctx.menu

    if cid == "cancel":
        _cancel(conv)
        return MSG_CANCELLED, ()
    if cid == "back":
        _back(conv, ctx)
        return None, ()

    if s in (State.start, State.closed, State.done):
        _start_order(conv, ctx)
    elif s is State.category:
        if cid == "cart":
            conv.state = State.cart_review
        else:
            _go(conv, State.item, category_id=cid.removeprefix("cat:"))
    elif s is State.item:
        if cid == "cart":
            conv.state = State.cart_review
        elif cid == "menu":
            _reset(conv, State.category)
        else:
            _go(conv, State.item_detail, item_id=cid.removeprefix("item:"), selected={})
    elif s is State.item_detail:
        if cid == "custom":
            item = menu.item(conv.data["item_id"])
            selected: dict[str, list[str]] = {}
            for oid in _default_option_ids(menu, item):
                selected.setdefault(menu.option(oid)[0].id, []).append(oid)
            conv.data["selected"] = selected
            conv.data.pop("group_index", None)
            _after_options(conv, ctx)
        else:
            qty = int(cid.removeprefix("add:")) if cid.startswith("add:") else 1
            item = menu.item(conv.data["item_id"])
            return _add_line(conv, ctx, qty, _default_option_ids(menu, item)), ()
    elif s is State.option_group:
        return _handle_option(conv, ctx, cid), ()
    elif s is State.quantity:
        qty = int(cid.removeprefix("qty:"))
        return _add_line(conv, ctx, qty, _selected_option_ids(conv, menu)), ()
    elif s is State.cart_review:
        if cid == "more":
            _reset(conv, State.category)
        elif cid == "edit":
            conv.state = State.edit_line
        elif cid == "proceed":
            problem = _checkout_problem(conv, ctx)
            if problem and problem[0] is State.cart_review:
                return problem[1], ()
            _proceed_to_checkout(conv, ctx)
    elif s is State.edit_line:
        _handle_edit(conv, cid)
    elif s is State.fulfilment:
        _choose_fulfilment(conv, Fulfilment(cid))
    elif s is State.slot:
        _go(conv, State.notes, slot=cid.removeprefix("slot:"))
    elif s is State.notes:
        conv.data.pop("notes", None)
        conv.state = State.confirm
    elif s is State.confirm:
        if cid == "edit":
            conv.state = State.cart_review
            return None, ()
        problem = _checkout_problem(conv, ctx)
        if problem:
            conv.state, notice = problem
            return notice, ()
        order = _build_order(conv, ctx)
        conv.cart = []
        conv.data = {}
        conv.state = State.done
        return None, (PlaceOrder(order),)
    return None, ()


def _revalidate(conv: Conversation, ctx: FlowContext) -> str | None:
    """The menu, the clock and slot bookings move while a conversation sits
    idle. Bring the conversation back to a state that can still be rendered
    and honoured. Returns a notice if something had to change."""
    s, d, menu = conv.state, conv.data, ctx.menu

    if s in (State.item_detail, State.option_group, State.quantity):
        item = menu.item(d.get("item_id", ""))
        if item is None or not item.available:
            _reset(conv, State.category)
            return "⚠ Questo prodotto non è più disponibile."
        if s is State.option_group and d.get("group_index", 0) >= len(menu.groups_for(item)):
            _reset(conv, State.category)
            return "⚠ Le opzioni di questo prodotto sono cambiate."

    if s is State.item and not menu.available_items(d.get("category_id", "")):
        _reset(conv, State.category)

    if s is State.edit_line:
        idx = d.get("line_index")
        if not conv.cart or (idx is not None and idx >= len(conv.cart)):
            d.pop("line_index", None)
            conv.state = State.cart_review

    if conv.state is State.cart_review:
        return _drop_unavailable(conv, menu)

    if conv.state is State.confirm:
        problem = _checkout_problem(conv, ctx)
        if problem:
            conv.state, notice = problem
            return notice
    return None


def handle(conv: Conversation, event: Event, ctx: FlowContext) -> Result:
    """Apply one customer event. Never mutates the input conversation."""
    conv = conv.model_copy(deep=True)

    stale = _revalidate(conv, ctx)
    if stale:
        # The customer acted on a screen that no longer holds: explain and
        # show the corrected screen instead of applying the event.
        screen = render(conv, ctx)
        return Result(conv, Screen(f"{stale}\n\n{screen.text}", screen.options, screen.accepts_text))

    current = render(conv, ctx)
    notice: str | None = None
    effects: tuple[PlaceOrder, ...] = ()

    if isinstance(event, Text):
        if conv.state is State.done:
            conv.state = State.start
        elif conv.state in (State.start, State.closed):
            pass  # any message (e.g. /start, "ciao") just shows the screen
        elif not current.accepts_text:
            notice = MSG_USE_BUTTONS
        elif conv.state is State.address:
            notice = _handle_address(conv, ctx, event.text)
        elif conv.state is State.notes:
            notes = clean_kitchen_notes(event.text)
            if notes and len(notes) > MAX_KITCHEN_NOTES:
                notice = f"Nota troppo lunga ({len(notes)}/{MAX_KITCHEN_NOTES} caratteri)."
            else:
                if notes:
                    conv.data["notes"] = notes
                else:
                    conv.data.pop("notes", None)
                conv.state = State.confirm
    elif event.id not in current.option_ids():
        notice = MSG_STALE
    else:
        notice, effects = _transition(conv, event.id, ctx)

    # Entering the cart or the summary always re-checks menu, minimum, slot.
    if not effects:
        changed = _revalidate(conv, ctx)
        if changed:
            notice = f"{notice}\n{changed}" if notice else changed

    screen = render(conv, ctx)
    if notice:
        screen = Screen(f"{notice}\n\n{screen.text}", screen.options, screen.accepts_text)
    return Result(conv, screen, effects)
