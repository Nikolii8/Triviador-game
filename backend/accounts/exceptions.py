from rest_framework.views import exception_handler as drf_exception_handler


def _as_list(value):
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def exception_handler(exc, context):
    """Wrap every DRF error response as {"errors": {field: [messages]}}."""
    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    data = response.data
    if isinstance(data, dict):
        errors = {key: _as_list(value) for key, value in data.items()}
    else:
        errors = {'non_field_errors': _as_list(data)}

    response.data = {'errors': errors}
    return response
