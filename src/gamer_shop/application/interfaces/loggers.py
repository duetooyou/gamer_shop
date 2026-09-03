from typing import NewType

from .logger import Logger

# Разные типы, чтобы dishka различала два трека при инжекте.
PaymentsLogger = NewType('PaymentsLogger', Logger)
DeliveryLogger = NewType('DeliveryLogger', Logger)
