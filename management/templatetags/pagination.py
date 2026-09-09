"""Template helpers for the shared management pager (_pagination.html)."""

from django import template

register = template.Library()


@register.filter
def elided_page_range(paginator, page_number):
    """Page numbers to link, with far-off runs collapsed to an ellipsis.

    Django templates can't call ``paginator.get_elided_page_range(page_number)``
    directly -- dotted lookups don't take arguments -- so this filter wraps it
    for `{% for page_num in paginator|elided_page_range:page_obj.number %}`.
    """
    return paginator.get_elided_page_range(page_number)
