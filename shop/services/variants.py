"""A registration product always has at least one option to register for.

Registration (registration.services.pricing, the iOS app's API, invoicing) works on
ProductVariant rows, not on products. A MEMBERSHIP/EVENT_FEE product with a single price
and no options of its own therefore gets one option named after the product, priced by
the product itself (variant price left blank), so it shows up in the registration
dropdown like any other.
"""

from shop.models import Product, ProductVariant

REGISTRATION_PRODUCT_TYPES = (Product.ProductType.MEMBERSHIP, Product.ProductType.EVENT_FEE)


def ensure_default_variant(product) -> ProductVariant | None:
    """Create the product-named option when a registration product has no options at all
    (active or not -- a product whose options were all switched off stays switched off).
    Returns it, or None when nothing was needed."""
    if product.product_type not in REGISTRATION_PRODUCT_TYPES or product.variants.exists():
        return None
    return ProductVariant.objects.create(product=product, name=product.name)
