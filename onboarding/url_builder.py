"""Build HH.ru vacancy search URLs from job preferences."""

from urllib.parse import urlencode, quote

# HH.ru area codes for major cities
_AREA = {
    "москва": "1", "moscow": "1",
    "санкт-петербург": "2", "спб": "2", "saint-petersburg": "2",
    "екатеринбург": "3",
    "новосибирск": "4",
    "казань": "88",
    "remote": "1",  # HH treats remote as Moscow area + schedule filter
}

_SCHEDULE = {
    "remote":  "remote",
    "hybrid":  "flexible",
    "office":  None,
}


def build_hh_url(role: str, city: str = "Москва",
                 salary: str = "", remote: str = "hybrid",
                 search_scope: str = "everywhere") -> str:
    """Build HH.ru search URL.

    search_scope:
      "name"       — vacancy title only (precise, fewer results)
      "everywhere" — title + description + company (broad, LLM scorer filters precision)
    """
    city_key = city.lower().strip()
    area = _AREA.get(city_key, "1")

    params: dict = {
        "text": role,
        "area": area,
        "search_field": search_scope if search_scope in ("name", "everywhere") else "everywhere",
    }

    schedule = _SCHEDULE.get(remote.lower())
    if schedule:
        params["schedule"] = schedule

    if salary:
        try:
            params["salary"] = int(salary.replace(" ", "").replace(" ", ""))
            params["only_with_salary"] = "true"
        except ValueError:
            pass

    return "https://hh.ru/search/vacancy?" + urlencode(params)
