from dataclasses import dataclass


@dataclass
class Item:
    id: int
    name: str
    price: float


def find_by_id(items: list[Item], item_id: int) -> Item | None:
    return next((i for i in items if i.id == item_id), None)


def filter_by_price(items: list[Item], max_price: float) -> list[Item]:
    return [i for i in items if i.price <= max_price]


class ItemService:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    def get(self, item_id: int) -> Item | None:
        return find_by_id(self._items, item_id)

    def add(self, item: Item) -> None:
        self._items.append(item)

    def remove(self, item_id: int) -> bool:
        before = len(self._items)
        self._items = [i for i in self._items if i.id != item_id]
        return len(self._items) < before

    def total_value(self) -> float:
        return sum(i.price for i in self._items)
