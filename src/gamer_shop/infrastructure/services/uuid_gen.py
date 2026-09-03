from uuid import UUID, uuid4


class UUID4Generator:
    def __call__(self) -> UUID:
        return uuid4()
