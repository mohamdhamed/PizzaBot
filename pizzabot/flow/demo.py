"""Demo restaurant: the synthetic reference menu from docs/design-v1.md §1.

Used by the local simulator (``python -m pizzabot.simulator``). Not real
data — replace with a real menu import once one exists.
"""

from __future__ import annotations

from datetime import time
from decimal import Decimal as D

from pizzabot.flow.menu import (
    Category,
    Menu,
    MenuItem,
    Option,
    OptionGroup,
    RestaurantSettings,
)

_PIZZA = ("dim", "impasto", "agg")


def demo_menu() -> Menu:
    groups = (
        OptionGroup("dim", "Dimensione", (
            Option("normale", "Normale"),
            Option("maxi", "Maxi", D("4.00")),
            Option("baby", "Baby", D("-1.00")),
        )),
        OptionGroup("impasto", "Impasto", (
            Option("classico", "Classico"),
            Option("integrale", "Integrale", D("1.00")),
            Option("sg", "Senza glutine", D("2.50")),
        )),
        OptionGroup("agg", "Aggiunte", (
            Option("bufala", "Mozzarella di bufala", D("2.00"), allergens=frozenset({7})),
            Option("crudo", "Prosciutto crudo", D("2.00")),
            Option("funghi", "Funghi", D("1.00")),
            Option("olive", "Olive", D("0.50")),
            Option("rucola", "Rucola", D("1.00")),
        ), min_select=0, max_select=3),
    )
    gl, lt, ce, se = 1, 7, 9, 12  # glutine, latte, sedano, solfiti

    def pizza(id_, cat, name, price, desc, allergens=(gl, lt)):
        return MenuItem(id_, cat, name, D(price), description=desc,
                        allergens=frozenset(allergens), option_group_ids=_PIZZA)

    items = (
        pizza("marinara", "classiche", "Marinara", "5.00", "pomodoro, aglio, origano", (gl,)),
        pizza("margherita", "classiche", "Margherita", "6.00", "pomodoro, mozzarella, basilico"),
        pizza("diavola", "classiche", "Diavola", "7.50", "pomodoro, mozzarella, salame piccante"),
        pizza("capricciosa", "classiche", "Capricciosa", "8.50",
              "pomodoro, mozzarella, prosciutto cotto, funghi, carciofi, olive"),
        pizza("4formaggi", "classiche", "Quattro formaggi", "8.50",
              "mozzarella, gorgonzola, fontina, parmigiano"),
        pizza("bufala", "speciali", "Bufala", "9.50", "pomodoro, mozzarella di bufala DOP, basilico"),
        pizza("tartufo", "speciali", "Tartufo", "12.00", "mozzarella, crema di tartufo, funghi porcini"),
        pizza("calzone", "calzoni", "Calzone classico", "8.00", "ricotta, mozzarella, prosciutto cotto"),
        MenuItem("suppli", "fritti", "Supplì", D("2.00"), description="riso, ragù, mozzarella",
                 allergens=frozenset({gl, lt, ce})),
        MenuItem("patatine", "fritti", "Patatine", D("3.50")),
        MenuItem("acqua", "bevande", "Acqua 50cl", D("1.50")),
        MenuItem("coca", "bevande", "Coca-Cola 33cl", D("2.50")),
        MenuItem("birra", "bevande", "Birra Moretti 33cl", D("3.50"),
                 allergens=frozenset({gl, se}), age_restricted=True),
        MenuItem("tiramisu", "dolci", "Tiramisù", D("5.00"),
                 description="mascarpone, savoiardi, caffè", allergens=frozenset({gl, 3, lt})),
    )
    categories = (
        Category("classiche", "Pizze classiche"),
        Category("speciali", "Pizze speciali"),
        Category("calzoni", "Calzoni"),
        Category("fritti", "Fritti"),
        Category("bevande", "Bevande"),
        Category("dolci", "Dolci"),
    )
    return Menu(categories=categories, items=items, option_groups=groups)


def demo_settings() -> RestaurantSettings:
    # Open all day so the demo works whenever it is tried.
    all_day = ((time(0, 0), time(23, 59)),)
    return RestaurantSettings(
        restaurant_id="demo",
        name="Pizzeria Demo",
        delivery_fee_eur=D("2.50"),
        min_order_eur=D("10.00"),
        delivery_postcodes=frozenset({"20900"}),  # Monza
        opening_hours={wd: all_day for wd in range(7)},
    )
