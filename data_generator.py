"""
data_generator.py — симулятор данных фотографа: случайные съёмки за период.

В реальном проекте на место этого файла встанет источник настоящих данных
(база заявок, CRM, выгрузка из календаря). Отчёт от этого не меняется:
ему нужен только список объектов Shoot.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence

# Тематики и пакеты: название, часы, кадров в ретуши, цена в евро.
PACKAGES: Dict[str, List[tuple]] = {
    "Сюжетная съёмка": [("Мини", 1, 15, 180), ("Стандарт", 2, 30, 320), ("Расширенный", 3, 45, 450)],
    "Мероприятие": [("2 часа", 2, 60, 360), ("4 часа", 4, 120, 640), ("День", 8, 250, 1150)],
    "Спорт": [("Тренировка", 1, 20, 200), ("Соревнования", 4, 80, 560)],
    "Художественная постановка": [("Соло", 2, 20, 380), ("Группа", 3, 35, 620)],
    "Love story": [("Прогулка", 1, 25, 250), ("Стандарт", 2, 40, 390)],
    "Свадьба": [("Роспись", 3, 80, 890), ("Полдня", 6, 200, 1650), ("Весь день", 10, 350, 2490)],
}

THEMES: List[str] = list(PACKAGES)

# Насколько часто встречается тематика (свадеб меньше, сюжетных больше).
THEME_WEIGHTS = {
    "Сюжетная съёмка": 30, "Мероприятие": 18, "Спорт": 12,
    "Художественная постановка": 12, "Love story": 16, "Свадьба": 12,
}

# Зоны выезда: фотограф работает в своём городе и окрестностях.
TRAVEL_ZONES = [
    ("В городе", 0, 70),
    ("Окрестности до 30 км", 40, 22),
    ("Дальний выезд до 80 км", 90, 8),
]

EXTRA_RETOUCH_PRICE = 6  # € за каждый кадр сверх пакета

STATUSES = [("Оплачено", 64), ("Предоплата", 20), ("Ожидает оплаты", 10), ("Отменена", 6)]

CLIENTS = {
    "DE": ["Lena Hoffmann", "Jonas Becker", "Mia Schulz", "Felix Wagner", "Hannah Koch",
           "Paul Richter", "Emma Neumann", "Lukas Braun", "Sophie Krüger", "Tim Wolf",
           "Studio Tanzwerk e.V.", "TSV Grün-Weiß"],
    "EN": ["Olivia Carter", "James Miller", "Chloe Evans", "Daniel Brooks", "Grace Turner"],
    "RU": ["Анна Смирнова", "Дмитрий Орлов", "Мария Волкова", "Илья Морозов",
           "Екатерина Лебедева", "Алексей Новиков"],
}
LANG_WEIGHTS = {"DE": 62, "EN": 14, "RU": 24}


@dataclass(frozen=True)
class Shoot:
    """Одна съёмка — строка детализации отчёта."""
    shoot_date: date
    client: str
    language: str
    theme: str
    package: str
    hours: int
    included_retouch: int
    zone: str
    package_price: int
    travel_fee: int
    extra_retouch: int
    status: str

    @property
    def retouch_fee(self) -> int:
        return self.extra_retouch * EXTRA_RETOUCH_PRICE

    @property
    def total(self) -> int:
        """Сумма к оплате. Отменённая съёмка выручки не даёт."""
        if self.status == "Отменена":
            return 0
        return self.package_price + self.travel_fee + self.retouch_fee


def _weighted(rng: random.Random, pairs: Sequence[tuple]) -> tuple:
    return rng.choices(pairs, weights=[p[-1] for p in pairs], k=1)[0]


def _shoot_day(rng: random.Random, start: date, end: date, theme: str) -> date:
    """Свадьбы и мероприятия чаще выпадают на выходные."""
    days = (end - start).days
    for _ in range(20):
        d = start + timedelta(days=rng.randint(0, days))
        weekend = d.weekday() >= 5
        if theme in ("Свадьба", "Мероприятие") and not weekend and rng.random() < 0.75:
            continue
        return d
    return start + timedelta(days=rng.randint(0, days))


def generate_shoots(
    start: date,
    end: date,
    themes: Optional[Sequence[str]] = None,
    seed: Optional[int] = None,
    shoots_per_week: float = 3.0,
) -> List[Shoot]:
    """
    Случайные съёмки за период [start; end], отсортированные по дате.

    themes — какие тематики оставить (по умолчанию все);
    seed — зерно случайности: одинаковый seed даёт одинаковый отчёт.
    """
    if end < start:
        raise ValueError("Дата окончания раньше даты начала")
    selected = [t for t in (THEMES if themes is None else themes) if t in PACKAGES]
    if not selected:
        raise ValueError("Не выбрано ни одной тематики")

    rng = random.Random(seed)
    weeks = ((end - start).days + 1) / 7
    count = max(1, round(weeks * shoots_per_week * rng.uniform(0.8, 1.2)))
    weights = [THEME_WEIGHTS[t] for t in selected]

    shoots: List[Shoot] = []
    for _ in range(count):
        theme = rng.choices(selected, weights=weights, k=1)[0]
        package, hours, included, price = rng.choice(PACKAGES[theme])
        language = rng.choices(list(LANG_WEIGHTS), weights=list(LANG_WEIGHTS.values()), k=1)[0]
        zone, fee, _ = _weighted(rng, TRAVEL_ZONES)
        extra = rng.choice([0, 0, 0, 5, 10, 15, 20]) if theme != "Мероприятие" else 0
        shoots.append(Shoot(
            shoot_date=_shoot_day(rng, start, end, theme),
            client=rng.choice(CLIENTS[language]),
            language=language,
            theme=theme,
            package=package,
            hours=hours,
            included_retouch=included,
            zone=zone,
            package_price=price,
            travel_fee=fee,
            extra_retouch=extra,
            status=_weighted(rng, STATUSES)[0],
        ))
    shoots.sort(key=lambda s: (s.shoot_date, s.client))
    return shoots
