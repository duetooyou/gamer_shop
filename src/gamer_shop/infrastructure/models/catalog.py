from sqlalchemy import Boolean, CheckConstraint, ForeignKey, Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gamer_shop.infrastructure.database import Base
from gamer_shop.infrastructure.models.mixins import TimestampMixin


class ProductORM(Base, TimestampMixin):
    """Товар каталога. SKU — естественный ключ, он же уходит поставщику."""

    __tablename__ = 'products'

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type: Mapped[str] = mapped_column(String(32), nullable=False, comment='topup/key/subscription/giftcard')
    # Деньги целым числом в рублях — как в контракте вебхука (amount: 500).
    price: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False, server_default=text("'RUB'"))
    image: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Поставщик товара: в одном заказе позиции расходятся по разным.
    supplier: Mapped[str] = mapped_column(
        String(8), nullable=False, server_default=text("'a'")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('true'))

    stock: Mapped['ProductStockORM'] = relationship(back_populates='product', lazy='noload')

    __table_args__ = (
        CheckConstraint('price >= 0', name='ck_products_price_non_negative'),
        # Витрина: покрывающий частичный индекс даёт Index Only Scan.
        Index(
            'ix_products_storefront',
            'type',
            'sku',
            postgresql_include=['name', 'price', 'currency'],
            postgresql_where=text('is_active'),
        ),
        # Та же витрина без фильтра по типу.
        Index(
            'ix_products_storefront_all',
            'sku',
            postgresql_include=['name', 'type', 'price', 'currency'],
            postgresql_where=text('is_active'),
        ),
    )


class ProductStockORM(Base):
    """Остаток по SKU.

    Отдельная таблица, а не колонка в products: остаток меняется на каждой
    выдаче, карточка — почти никогда. Даёт дешёвые HOT-update.
    """

    __tablename__ = 'product_stock'

    sku: Mapped[str] = mapped_column(
        ForeignKey('products.sku', ondelete='CASCADE'), primary_key=True
    )
    available_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))
    reserved_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('0'))

    product: Mapped['ProductORM'] = relationship(back_populates='stock', lazy='noload')

    __table_args__ = (
        CheckConstraint('available_count >= 0', name='ck_stock_available_non_negative'),
        CheckConstraint('reserved_count >= 0', name='ck_stock_reserved_non_negative'),
    )
    # fillfactor задаётся в миграции: SQLAlchemy не умеет storage parameters.
