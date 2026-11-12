"""The fixed systemd and weather collectors behind an approved read plan, and their preview. No model tools."""
import json

from .analysis_contract import AnalysisDenied, terminal_text
from . import systemd_context, weather_context
from .terminal_consent import confirm, fresh_input
from .question_consent import QuestionConsent

WEATHER_HINTS = {
    "not-found": "City not found. Give its base name: --weather CITY.",
    "network": "Could not connect to Open-Meteo. Check the network and try again.",
    "timeout": "Fetching the weather timed out. Try again later.",
    "rate-limit": "Open-Meteo limited the number of requests. Try again later.",
    "unavailable": "The weather source is unavailable. Try again later.",
    "stale": "The source returned out-of-date weather data. It was not passed to Claude.",
    "input": "Invalid city. Give a name: --weather CITY.",
    "response": "Could not verify the weather data. It was not passed to Claude.",
}


def _read(prompt, reader, limit=128):
    answer = fresh_input(prompt, max_bytes=limit) if reader is input else reader(prompt)
    if type(answer) is not str or len(answer.encode("utf-8")) > limit:
        raise ValueError("context input")
    return answer.strip()


def _check_question(question_consent):
    if question_consent is not None:
        if type(question_consent) is not QuestionConsent:
            raise AnalysisDenied("consent")
        question_consent.check()


def _check_source(check, question_consent):
    _check_question(question_consent)
    check()
    _check_question(question_consent)


def _weather(city, args, reader, *, check=lambda: None, question_consent=None):
    _check_question(question_consent)
    if question_consent is not None:
        question_consent.require_weather()
    quiet = question_consent is not None and not getattr(args, "details", False)
    if args.preview and not city:
        if not quiet:
            print("Weather: planned fetch from Open-Meteo; the city must be chosen before connecting. "
                  "Offline preview: no connection and no weather data.")
        return None
    while not city:
        city = _read("City name: ", reader)
        _check_question(question_consent)
    # Validate before any terminal interpolation, DNS or HTTP request.
    weather_context.validate_city(city)
    if args.preview:
        if not quiet:
            print("Weather: planned fetch from Open-Meteo for " + terminal_text(city)
                  + ". Offline preview: no connection and no weather data.")
        return None
    if not quiet:
        print("Weather: Open-Meteo will receive the city " + json.dumps(city, ensure_ascii=False)
              + " and the IP address of the connection.")
    if question_consent is None and not confirm("weather", reader=reader):
        raise AnalysisDenied("consent")
    _check_source(check, question_consent)
    candidates = weather_context.geocode(city)
    _check_source(check, question_consent)
    if not candidates:
        print("City not found. Run again with --weather CITY.")
        raise AnalysisDenied("input")
    if len(candidates) == 1:
        selected = candidates[0]
    else:
        print("Choose a city (at most 5 results):")
        for index, candidate in enumerate(candidates, 1):
            print(str(index) + ". " + terminal_text(", ".join(filter(None, (
                candidate["name"], candidate["admin1"], candidate["country"])))))
        while True:
            answer = _read("City number, or n/no to cancel: ", reader, 64)
            _check_source(check, question_consent)
            if answer.casefold() in ("n", "no"):
                raise AnalysisDenied("consent")
            if answer in {str(i) for i in range(1, len(candidates) + 1)}:
                selected = candidates[int(answer) - 1]
                break
            print("Enter a number from the list, or n/no.")
    _check_source(check, question_consent)
    result = weather_context.current(selected)
    _check_source(check, question_consent)
    return result


def _systemd(scope, view, args, reader, *, check=lambda: None, question_consent=None):
    """Read under the active question consent, or a fresh standalone read yes."""
    _check_question(question_consent)
    quiet = question_consent is not None and not getattr(args, "details", False)
    if scope not in ("system", "user", "both"):
        raise ValueError("scope")
    listing = systemd_context.listing_for(view)
    scopes = ("system", "user") if scope == "both" else (scope,)
    if question_consent is not None:
        for selected in scopes:
            question_consent.require_systemd(selected)
    if not quiet:
        print("Local access: /usr/bin/systemctl; scope: " + ", ".join(scopes)
              + "; view: " + view + " (" + listing + ").")
        print("Reads unit names and states; services are not changed.")
    if args.preview:
        if not quiet:
            print("Offline preview: systemd was not read.")
        return []
    if question_consent is None and not confirm("systemd-read", reader=reader):
        raise AnalysisDenied("consent")
    context = []
    for selected in scopes:
        _check_source(check, question_consent)
        item = systemd_context.collect(scope=selected, view=view)
        _check_source(check, question_consent)
        if item["status"] != "ok":
            status = item["status"] if item["status"] in systemd_context.STATUSES else "invalid"
            print("Could not read systemd (scope: " + selected + ", status: " + status + ").")
            raise AnalysisDenied("input")
        context.append(item)
    return context


def preview_context(context, *, details=False):
    """Show source/scope/count; exact observations are available with --details."""
    for item in context:
        if item["kind"] == "systemd":
            print("\nSystemd: " + ("system" if item["scope"] == "system" else "user")
                  + ", " + ("installed units" if item["listing"] == "unit-files" else "loaded units")
                  + ", " + item["view"] + "; " + item["observed_at"])
            if item["status"] != "ok":
                print("Could not read this scope (" + item["status"] + ").")
                continue
            if details:
                for unit in item["items"]:
                    print("  " + unit["name"] + "  " + unit["state"]
                          + (" / " + unit["substate"] if unit["substate"] else ""))
            print("For the answer: " + str(len(item["items"])) + " of " + str(item["total"])
                  + (" (list shortened)." if item["truncated"] else "."))
        elif item["kind"] == "weather":
            print("\nWeather: " + terminal_text(", ".join(filter(None, (
                item["location"]["name"], item["location"]["admin1"], item["location"]["country"])))))
            from .weather_context import local_time, shown
            print("Open-Meteo, model estimate; data time: " + local_time(item["valid_at"]))
            current = item["current"]
            if details:
                temperature, feels, rain, wind = shown(current)
                print("Temperature: " + temperature + "; feels like: " + feels + "; precipitation: " + rain
                      + "; wind: " + wind + "; WMO weather code: " + str(current["weather_code"]))
                print("Data period: " + str(current["interval_seconds"]) + " s.")
            print("Sources: " + item["source_url"] + "; geocoding https://www.geonames.org/")
    if context:
        print()
