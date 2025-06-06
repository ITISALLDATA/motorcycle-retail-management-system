# templatetags/custom_filters.py
from django import template
from urllib.parse import urlencode

register = template.Library()

@register.filter
def build_url(request, page_number):
    """
    Build pagination URL preserving existing GET parameters
    Usage: {{ request|build_url:page_number }}
    """
    params = request.GET.copy()
    params['page'] = page_number
    return f"?{params.urlencode()}"

@register.filter
def get_page_range(paginator, current_page, window=2):
    """
    Get smart page range for pagination
    Usage: {{ paginator|get_page_range:page_obj.number }}
    """
    total_pages = paginator.num_pages
    start = max(1, current_page - window)
    end = min(total_pages + 1, current_page + window + 1)
    return range(start, end)