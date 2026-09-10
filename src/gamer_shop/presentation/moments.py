"""Разбор меток времени из строки запроса.

Спрашивают и датой, и моментом: «что было десятого сентября» — такой же
законный вопрос, как «что было в 16:30:11». Дата — это не момент, а сутки,
поэтому она разворачивается в границу: начало периода берётся с их начала,
конец периода и срез «на дату» — с их конца, чтобы в ответ попал весь день.

Без зоны считаем UTC: сравнивать метку придётся с timestamptz, а угадывать
чужой часовой пояс хуже, чем назвать свой.
"""

from datetime import UTC, date, datetime, time

from litestar.exceptions import ValidationException

# Что принимаем: '2026-09-10', '2026-09-10T16:30:11',
# '2026-09-10T16:30:11.579959+00:00', '2026-09-10T16:30:11Z'.
_HINT = (
    'ожидается дата 2026-09-10 либо момент 2026-09-10T16:30:11Z '
    '(допустимо без зоны — тогда UTC)'
)


def parse_moment(raw: str | None, *, end_of_day: bool = False) -> datetime | None:
    """Строка запроса -> момент. None остаётся None: параметр необязателен."""
    if raw is None:
        return None

    value = raw.strip()
    if not value:
        return None

    try:
        parsed: datetime | date = date.fromisoformat(value)
    except ValueError:
        try:
            # 'Z' питон принимает только с 3.11, но подстраховаться дешевле,
            # чем разбираться, почему отвалился один клиент из десяти.
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError as exc:
            raise ValidationException(
                f'не разобрать метку времени «{raw}»: {_HINT}'
            ) from exc

    if not isinstance(parsed, datetime):
        # Дали дату — разворачиваем в границу суток.
        edge = time.max if end_of_day else time.min
        parsed = datetime.combine(parsed, edge)

    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
