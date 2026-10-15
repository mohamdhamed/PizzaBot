"""Menu import: Excel → normalized rows (dish_name, price_eur, category, available, ingredients)."""

from pizzabot.menu.normalizer import normalize_dataframe

__all__ = ["normalize_dataframe"]
