"""Question context selection with synthetic collectors; no host or network I/O."""
import contextlib
import copy
import io
from types import SimpleNamespace
import unittest
from tests.profile_fixture import DEFAULT_PROFILE
from unittest.mock import Mock, call, patch

from ai_desktop import ask_cli, ask_context, assistant_flow, terminal_consent
from ai_desktop.analysis_contract import AnalysisDenied
from ai_desktop.weather_context import WeatherError
from ai_desktop.question_consent import QuestionConsent


LOCATION = {"id": 1000001, "name": "Example City", "country": "Exampleland",
            "admin1": "Example Region", "latitude": 45.00012, "longitude": 10.00988}
OTHER = {"id": 2, "name": "Other City", "country": "USA",
         "admin1": "Other Region", "latitude": 40.0, "longitude": 30.0}
WEATHER = {"kind": "weather", "source": "Open-Meteo",
           "source_url": "https://open-meteo.com/en/docs",
           "observed_at": "2026-10-30T12:12:00Z", "valid_at": "2026-10-30T12:00:00Z",
           "basis": "model", "location": LOCATION,
           "current": {"temperature_c": 7.2, "apparent_temperature_c": 5.5,
                       "precipitation_mm": 0.1, "weather_code": 3, "wind_kmh": 11.8,
                       "interval_seconds": 900}}


def options(**kwargs):
    return SimpleNamespace(**dict({"systemd": None, "weather": None,
                                   "systemd_state": None, "no_context": False,
                                   "preview": False, "details": False}, **kwargs))


def gather(args, reader):
    """What the live path does for explicit --systemd/--weather: the same collectors, then the same preview."""
    context = []
    if args.systemd:
        context.extend(ask_context._systemd(args.systemd, args.systemd_state or "installed", args, reader))
    if args.weather:
        try:
            item = ask_context._weather(args.weather, args, reader)
        except ask_context.weather_context.WeatherError as exc:
            print(ask_context.WEATHER_HINTS[exc.reason])        # as assistant_flow.collect does, before refusing
            raise AnalysisDenied("input") from None
        if item is not None:
            context.append(item)
    ask_context.preview_context(context, details=args.details)
    return context


def inventory(scope="system", view="enabled"):
    unit_file = view in ("enabled", "installed")
    state = {"installed": "disabled", "all": "inactive"}.get(view, view)
    return {"kind": "systemd", "source": "systemctl", "scope": scope, "view": view,
            "listing": "unit-files" if unit_file else "units",
            "observed_at": "2026-10-30T12:12:00Z", "status": "ok", "truncated": False,
            "total": 1, "items": [{"name": "synthetic.service", "state": state,
                                    "substate": None if unit_file else
                                    ("running" if state == "active" else "dead")}]}


class AskContextTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch("ai_desktop.agent_profiles.resolve", return_value=DEFAULT_PROFILE))
        self.output = io.StringIO()
        self.enterContext(contextlib.redirect_stdout(self.output))
        self.collect = self.enterContext(patch.object(ask_context.systemd_context, "collect"))
        self.geocode = self.enterContext(patch.object(ask_context.weather_context, "geocode"))
        self.current = self.enterContext(patch.object(ask_context.weather_context, "current"))
        self.confirm = self.enterContext(patch.object(ask_context, "confirm", return_value=True))
        snapshot = assistant_flow.AccessSnapshot(Mock(),
            {"claude": True, "weather": True, "systemd": ["system", "user"]},
            {"files": [], "weather": True, "systemd": ["system", "user"]})
        self.enterContext(patch.object(assistant_flow, "capture_access", return_value=snapshot))
        self.reader = Mock(side_effect=AssertionError("unexpected prompt"))

    def assert_no_collectors(self):
        self.collect.assert_not_called()
        self.geocode.assert_not_called()
        self.current.assert_not_called()

    def permit(self, *, clock=None, approved=True, systemd=("system", "user"), weather=True):
        snapshot = Mock()
        snapshot.sources = {"claude": True, "weather": weather, "systemd": list(systemd)}
        snapshot.access = {"files": [], "weather": weather, "systemd": list(systemd)}
        permit = QuestionConsent("SYNTHETIC question", snapshot, clock=clock)
        if approved:
            permit.approve()
        return permit, snapshot

    def test_approved_question_systemd_is_quiet_without_new_source_consent(self):
        permit, _ = self.permit()
        self.collect.side_effect = lambda **kwargs: inventory(**kwargs)
        check = Mock()
        result = ask_context._systemd("both", "enabled", options(), self.reader,
                                     check=check, question_consent=permit)
        self.assertEqual(result, [inventory("system"), inventory("user")])
        self.assertEqual(check.call_count, 4)
        self.confirm.assert_not_called()
        self.reader.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_approved_question_weather_is_quiet_and_preserves_exact_observations(self):
        permit, _ = self.permit()
        self.geocode.return_value = [LOCATION]
        self.current.return_value = WEATHER
        check = Mock()
        result = ask_context._weather("Example City", options(), self.reader,
                                     check=check, question_consent=permit)
        self.assertEqual(result, WEATHER)
        self.assertEqual(check.call_count, 4)
        self.confirm.assert_not_called()
        self.reader.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_invalid_or_inactive_question_grant_never_falls_back_to_source_prompt(self):
        inactive, _ = self.permit(approved=False)
        for permit in (True, False, Mock(), object(), inactive):
            for operation in (lambda: ask_context._systemd("system", "enabled", options(), self.reader,
                                                          question_consent=permit),
                              lambda: ask_context._weather("Example City", options(), self.reader,
                                                          question_consent=permit)):
                with self.subTest(permit=type(permit).__name__), self.assertRaises(AnalysisDenied) as error:
                    operation()
                self.assertEqual(error.exception.reason, "consent")
        self.assert_no_collectors()
        self.confirm.assert_not_called()
        self.reader.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_question_grant_cannot_enable_unavailable_scope_or_weather(self):
        for scope in ("user", "both"):
            permit, _ = self.permit(systemd=("system",))
            with self.subTest(scope=scope), self.assertRaises(AnalysisDenied):
                ask_context._systemd(scope, "enabled", options(), self.reader, question_consent=permit)
            self.collect.assert_not_called()
        permit, _ = self.permit(weather=False)
        with self.assertRaises(AnalysisDenied):
            ask_context._weather("Other City", options(), self.reader, question_consent=permit)
        self.assert_no_collectors()
        self.confirm.assert_not_called()
        self.reader.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_question_deadline_between_systemd_scopes_stops_without_partial_result(self):
        clock = [0]
        permit, _ = self.permit(clock=lambda: clock[0])
        def collect(**kwargs):
            clock[0] = 181
            return inventory(**kwargs)
        self.collect.side_effect = collect
        with self.assertRaises(AnalysisDenied) as error:
            ask_context._systemd("both", "enabled", options(), self.reader, question_consent=permit)
        self.assertEqual(error.exception.reason, "consent")
        self.collect.assert_called_once_with(scope="system", view="enabled")
        self.confirm.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_question_revoked_during_geocode_prevents_forecast_and_location_prompt(self):
        permit, snapshot = self.permit()
        def geocode(city):
            snapshot.access["weather"] = False
            return [LOCATION, OTHER]
        self.geocode.side_effect = geocode
        with self.assertRaises(AnalysisDenied) as error:
            ask_context._weather("Other City", options(), self.reader, question_consent=permit)
        self.assertEqual(error.exception.reason, "consent")
        self.current.assert_not_called()
        self.reader.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_question_keeps_ambiguous_city_choice_and_deadline_after_choice(self):
        self.geocode.return_value = [LOCATION, OTHER]
        clock = [0]
        permit, _ = self.permit(clock=lambda: clock[0])
        def choose(prompt):
            clock[0] = 181
            return "2"
        with self.assertRaises(AnalysisDenied) as error:
            ask_context._weather("Other City", options(), choose, question_consent=permit)
        self.assertEqual(error.exception.reason, "consent")
        self.geocode.assert_called_once_with("Other City")
        self.current.assert_not_called()
        self.confirm.assert_not_called()
        self.assertIn("Choose a city", self.output.getvalue())
        self.assertIn("Other City, Other Region, Otherland", self.output.getvalue())
        self.assertNotIn("IP address", self.output.getvalue())
        self.assertNotIn("Weather:", self.output.getvalue())

    def test_approved_question_uses_chosen_city_without_automatic_replacement(self):
        permit, _ = self.permit()
        self.geocode.return_value = [LOCATION, OTHER]
        self.current.return_value = WEATHER
        reader = Mock(side_effect=["?", "2"])
        ask_context._weather("Other City", options(), reader, question_consent=permit)
        self.current.assert_called_once_with(OTHER)
        self.assertEqual(reader.call_count, 2)
        self.confirm.assert_not_called()
        self.assertIn("Enter a number from the list", self.output.getvalue())
        self.assertNotIn("Weather:", self.output.getvalue())

    def test_question_permission_and_policy_checks_remain_after_current_data(self):
        permit, _ = self.permit()
        revoked = [False]
        self.geocode.return_value = [LOCATION]
        def current(location):
            revoked[0] = True
            return WEATHER
        def check():
            if revoked[0]:
                raise assistant_flow.AccessDenied()
        self.current.side_effect = current
        with self.assertRaises(assistant_flow.AccessDenied):
            ask_context._weather("Example City", options(), self.reader, check=check, question_consent=permit)
        self.current.assert_called_once_with(LOCATION)
        self.confirm.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_question_expiring_during_forecast_does_not_return_weather(self):
        clock = [0]
        permit, _ = self.permit(clock=lambda: clock[0])
        self.geocode.return_value = [LOCATION]
        def current(location):
            clock[0] = 181
            return WEATHER
        self.current.side_effect = current
        with self.assertRaises(AnalysisDenied) as error:
            ask_context._weather("Example City", options(), self.reader, question_consent=permit)
        self.assertEqual(error.exception.reason, "consent")
        self.current.assert_called_once_with(LOCATION)
        self.confirm.assert_not_called()
        self.assertEqual(self.output.getvalue(), "")

    def test_quiet_question_still_reports_failed_systemd_and_withholds_partial_data(self):
        permit, _ = self.permit()
        failed = inventory("user")
        failed.update(status="timeout", total=None, items=[])
        self.collect.side_effect = [inventory("system"), failed]
        with self.assertRaises(AnalysisDenied) as error:
            ask_context._systemd("both", "enabled", options(), self.reader, question_consent=permit)
        self.assertEqual(error.exception.reason, "input")
        self.assertEqual(self.collect.call_count, 2)
        self.confirm.assert_not_called()
        self.assertEqual(self.output.getvalue(),
                         "Could not read systemd (scope: user, status: timeout).\n")

    def test_question_details_keep_source_descriptions_without_new_consent(self):
        permit, _ = self.permit()
        self.collect.return_value = inventory()
        self.geocode.return_value = [LOCATION]
        self.current.return_value = WEATHER
        ask_context._systemd("system", "enabled", options(details=True), self.reader,
                             question_consent=permit)
        ask_context._weather("Example City", options(details=True), self.reader, question_consent=permit)
        self.assertIn("Local access:", self.output.getvalue())
        self.assertIn("Weather: Open-Meteo", self.output.getvalue())
        self.confirm.assert_not_called()
        self.reader.assert_not_called()

    def test_systemd_question_collects_requested_scope_and_state_only(self):
        self.collect.side_effect = lambda **kwargs: inventory(**kwargs)
        cases = ((dict(systemd="system", systemd_state="enabled"), [call(scope="system", view="enabled")]),
                 (dict(systemd="user", systemd_state="active"), [call(scope="user", view="active")]),
                 (dict(systemd="system", systemd_state="inactive"), [call(scope="system", view="inactive")]),
                 (dict(systemd="both", systemd_state="enabled"),
                  [call(scope="system", view="enabled"), call(scope="user", view="enabled")]))
        for selected, expected in cases:
            self.collect.reset_mock()
            self.confirm.reset_mock()
            self.output.seek(0)
            self.output.truncate(0)
            with self.subTest(selected=selected):
                context = gather(options(details=True, **selected), self.reader)
                self.confirm.assert_called_once_with("systemd-read", reader=self.reader)
                self.assertEqual(self.collect.call_args_list, expected)
                self.assertEqual(len(context), len(expected))
                for result in context:
                    label = "Systemd: system" if result["scope"] == "system" else "Systemd: user"
                    self.assertIn(label, self.output.getvalue())
                    self.assertIn(result["items"][0]["name"], self.output.getvalue())
        self.geocode.assert_not_called()
        self.current.assert_not_called()

    def test_local_read_requires_fresh_consent_for_inferred_and_explicit_selection(self):
        events = []
        def consent(action, **kwargs):
            self.collect.assert_not_called()
            self.assertEqual(action, "systemd-read")
            self.assertIn("/usr/bin/systemctl", self.output.getvalue())
            self.assertIn("Reads unit names and states", self.output.getvalue())
            events.append("consent")
            return True
        self.confirm.side_effect = consent
        self.collect.side_effect = lambda **kwargs: events.append(("collect", kwargs)) or inventory(**kwargs)
        for opts in (options(systemd="user", systemd_state="active"),):
            events.clear()
            self.collect.reset_mock()
            result = gather(opts, self.reader)
            self.assertEqual(result, [inventory(scope="user", view="active")])
            self.assertEqual(events, ["consent", ("collect", {"scope": "user", "view": "active"})])
            self.assertIn("scope: user; view: active (units)", self.output.getvalue())
        self.geocode.assert_not_called()
        self.current.assert_not_called()

    def test_local_read_denial_or_interruption_prevents_all_collectors(self):
        for failure in (False, EOFError(), KeyboardInterrupt()):
            self.confirm.side_effect = failure if isinstance(failure, BaseException) else None
            self.confirm.return_value = failure
            expected = type(failure) if isinstance(failure, BaseException) else AnalysisDenied
            for opts in (options(systemd="system", systemd_state="enabled"),
                         options(systemd="both", weather="Example City")):
                with self.subTest(failure=failure, opts=opts), self.assertRaises(expected):
                    gather(opts, self.reader)
                self.assert_no_collectors()

    def test_unknown_local_read_answers_repeat_before_any_collection(self):
        answers = iter(["", "?", "yes"])
        def reader(prompt):
            self.collect.assert_not_called()
            self.assertEqual(prompt, terminal_consent.PROMPTS["systemd-read"])
            return next(answers)
        self.confirm.side_effect = terminal_consent.confirm
        self.collect.return_value = inventory()
        with patch.object(terminal_consent.sys.stdin, "isatty", return_value=True), \
                patch.object(terminal_consent.sys.stdout, "isatty", return_value=True):
            self.assertEqual(gather(options(systemd="system", systemd_state="enabled"), reader), [inventory()])
        self.collect.assert_called_once_with(scope="system", view="enabled")
        self.assertEqual(self.output.getvalue().count("Type y or n."), 1)     # Enter alone only repeats the prompt

    def test_preview_plans_systemd_and_weather_without_tty_reads_or_permissions(self):
        with patch.object(ask_cli.os, "getuid", return_value=1000), \
                patch.object(ask_cli.os, "geteuid", return_value=1000), \
                patch.object(ask_cli.sys.stdin, "isatty", return_value=False), \
                patch.object(ask_cli.sys.stdout, "isatty", return_value=False), \
                patch.object(ask_cli, "installation_ready") as ready, \
                patch.object(ask_cli, "managed_session") as session, \
                patch.object(ask_cli, "send_once") as send:
            for argv in (["--question", "Pokaż aktywne usługi systemd", "--preview"],
                         ["--question", "Jaka pogoda?", "--preview"],
                         ["--question", "Pytanie", "--preview", "--systemd", "both", "--systemd-state", "failed",
                          "--weather", "Example City"]):
                with self.subTest(argv=argv):
                    self.assertEqual(ask_cli.main(argv, reader=self.reader), 0)
            ready.assert_not_called()
            session.assert_not_called()
            send.assert_not_called()
        self.assert_no_collectors()
        self.reader.assert_not_called()
        self.confirm.assert_not_called()
        self.assertIn("systemd: system, user", self.output.getvalue())
        self.assertIn("no model, no directory listing and no reading of the selected data", self.output.getvalue())

    def test_declined_question_never_reads_sources_or_reaches_claude(self):
        self.reader.side_effect = ["no"]
        with patch.object(ask_cli.os, "getuid", return_value=1000), \
                patch.object(ask_cli.os, "geteuid", return_value=1000), \
                patch.object(ask_cli.sys.stdin, "isatty", return_value=True), \
                patch.object(ask_cli.sys.stdout, "isatty", return_value=True), \
                patch.object(ask_cli, "installation_ready") as ready, \
                patch.object(ask_cli, "managed_session") as session, \
                patch.object(ask_cli, "send_once") as send:
            self.assertEqual(ask_cli.main(["--question", "Pytanie", "--systemd", "both"], reader=self.reader), 2)
            ready.assert_not_called()
            session.assert_not_called()
            send.assert_not_called()
        self.assert_no_collectors()
        self.confirm.assert_not_called()
        self.reader.assert_called_once_with(terminal_consent.PROMPTS["ask-question"])

    def test_weather_preview_is_offline_and_does_not_request_consent(self):
        result = gather(options(weather="Example City", preview=True), self.reader)
        self.assertEqual(result, [])
        self.assertIn("Example City", self.output.getvalue())
        self.assertIn("no connection", self.output.getvalue())
        self.assert_no_collectors()
        self.confirm.assert_not_called()
        self.reader.assert_not_called()

    def test_weather_refusal_prevents_even_geocoding(self):
        self.confirm.return_value = False
        with self.assertRaises(AnalysisDenied) as error:
            gather(options(weather="Example City"), self.reader)
        self.assertEqual(error.exception.reason, "consent")
        self.confirm.assert_called_once_with("weather", reader=self.reader)
        self.assert_no_collectors()
        self.assertIn("Open-Meteo", self.output.getvalue())
        self.assertIn("IP address", self.output.getvalue())

    def test_ambiguous_city_invalid_selection_retries_and_n_cancels(self):
        self.geocode.return_value = [LOCATION, OTHER]
        reader = Mock(side_effect=["", "0", "3", "1.0", "n"])
        with self.assertRaises(AnalysisDenied) as error:
            gather(options(weather="Example City"), reader)
        self.assertEqual(error.exception.reason, "consent")
        self.assertEqual(reader.call_count, 5)
        self.geocode.assert_called_once_with("Example City")
        self.current.assert_not_called()
        self.assertEqual(self.output.getvalue().count("Enter a number from the list, or n/no."), 4)

    def test_selected_weather_exact_observations_are_shown_and_returned(self):
        self.geocode.return_value = [LOCATION, OTHER]
        context = copy.deepcopy(WEATHER)
        context["location"] = OTHER
        self.current.return_value = context
        reader = Mock(side_effect=["?", "2"])
        result = gather(options(weather="Other City", details=True), reader)
        self.geocode.assert_called_once_with("Other City")
        self.current.assert_called_once_with(OTHER)
        self.assertEqual(result, [context])
        output = self.output.getvalue()
        self.assertIn("Weather: Other City, Other Region, Otherland", output)
        self.assertIn("2026-10-30T12:00:00Z", output)
        self.assertIn("Temperature: 7.2 °C; feels like: 5.5 °C; precipitation: 0.1 mm; wind: 11.8 km/h", output)
        self.assertIn("WMO weather code: 3", output)
        self.assertIn("900 s", output)
        self.assertIn(context["source_url"], output)
        self.assertIn("model estimate", output)
        self.assertIn("https://www.geonames.org/", output)

    def test_default_context_summary_omits_rows_numbers_and_json(self):
        systemd = inventory()
        systemd.update(total=2, truncated=True)
        self.collect.return_value = systemd
        self.geocode.return_value = [LOCATION]
        self.current.return_value = WEATHER
        result = gather(options(systemd="system", systemd_state="enabled", weather="Example City"), self.reader)
        self.assertEqual(result, [systemd, WEATHER])
        output = self.output.getvalue()
        self.assertIn("Systemd: system", output)
        self.assertIn("For the answer: 1 of 2", output)
        self.assertIn("list shortened", output)
        self.assertIn("Weather: Example City, Example Region, Exampleland", output)
        self.assertIn("Open-Meteo", output)
        self.assertIn(WEATHER["source_url"], output)
        for hidden in ("synthetic.service", "Temperature:", "7.2", "5.5", "0.1", "11.8", "900 s",
                       "latitude", "temperature_c", "{", "}"):
            with self.subTest(hidden=hidden):
                self.assertNotIn(hidden, output)

    def test_consent_precedes_provider_queries(self):
        events = []
        self.confirm.side_effect = lambda *a, **k: events.append("consent") or True
        self.geocode.side_effect = lambda city: events.append(("geocode", city)) or [LOCATION]
        self.current.side_effect = lambda location: events.append(("current", location)) or WEATHER
        self.assertEqual(gather(options(weather="Example City"), self.reader), [WEATHER])
        self.assertEqual(events, ["consent", ("geocode", "Example City"), ("current", LOCATION)])
        self.reader.assert_not_called()
        self.collect.assert_not_called()

    def test_invalid_city_refuses_before_terminal_interpolation_or_io(self):
        for city in ("\x1b[2J", "Example City\nSECRET", "\u202eExample City", "x" * 121):
            with self.subTest(city=repr(city)), self.assertRaises(AnalysisDenied):
                gather(options(weather=city), self.reader)
        self.assert_no_collectors()
        self.confirm.assert_not_called()
        self.assertNotIn("SECRET", self.output.getvalue())
        self.assertNotIn("\x1b", self.output.getvalue())

    def test_provider_failure_cancels_cli_before_claude_and_hides_raw_exception(self):
        self.reader.side_effect = lambda prompt: "yes" if prompt == terminal_consent.PROMPTS["ask-question"] else self.fail("unexpected source consent")
        self.geocode.return_value = [LOCATION]
        self.current.return_value = WEATHER
        with patch.object(ask_cli.os, "getuid", return_value=1000), \
                patch.object(ask_cli.os, "geteuid", return_value=1000), \
                patch.object(ask_cli.sys.stdin, "isatty", return_value=True), \
                patch.object(ask_cli.sys.stdout, "isatty", return_value=True), \
                patch.object(ask_cli, "installation_ready") as ready, \
                patch.object(ask_cli, "managed_session") as session, \
                patch.object(ask_cli, "send_once") as send:
            for collector in (self.geocode, self.current):
                for exception in (WeatherError("network"), WeatherError("stale"),
                                  OSError("PRIVATE UPSTREAM TOKEN")):
                    with self.subTest(collector=collector, exception=exception):
                        collector.side_effect = exception
                        self.assertEqual(ask_cli.main(["--question", "Pytanie", "--weather", "Example City"],
                                                      reader=self.reader), 2)
                        collector.side_effect = None
            ready.assert_not_called()
            session.assert_not_called()
            send.assert_not_called()
        self.confirm.assert_not_called()
        self.assertEqual(self.reader.call_count, 6)
        self.assertNotIn("PRIVATE UPSTREAM TOKEN", self.output.getvalue())
        self.assertNotIn("Traceback", self.output.getvalue())
        self.assertIn("Check the network", self.output.getvalue())
        self.assertIn("out-of-date", self.output.getvalue())

    def test_incomplete_systemd_result_refuses_and_does_not_send_to_claude(self):
        self.reader.side_effect = ["yes"]
        result = inventory()
        result.update(status="timeout", total=None, items=[])
        self.collect.return_value = result
        with patch.object(ask_cli.os, "getuid", return_value=1000), \
                patch.object(ask_cli.os, "geteuid", return_value=1000), \
                patch.object(ask_cli.sys.stdin, "isatty", return_value=True), \
                patch.object(ask_cli.sys.stdout, "isatty", return_value=True), \
                patch.object(ask_cli, "installation_ready") as ready, \
                patch.object(ask_cli, "managed_session") as session, \
                patch.object(ask_cli, "send_once") as send:
            self.assertEqual(ask_cli.main(["--question", "Wypisz włączone usługi", "--systemd", "system",
                                           "--systemd-state", "enabled"], reader=self.reader), 2)
            ready.assert_not_called()
            session.assert_not_called()
            send.assert_not_called()
        self.confirm.assert_not_called()
        self.reader.assert_called_once_with(terminal_consent.PROMPTS["ask-question"])
        self.assertIn("Could not read systemd (scope: system, status: timeout).", self.output.getvalue())
        self.assertNotIn("For the answer:", self.output.getvalue())
        self.geocode.assert_not_called()
        self.current.assert_not_called()

    def test_second_scope_failure_does_not_release_first_scope_context(self):
        failed = inventory(scope="user")
        failed.update(status="unavailable", total=None, items=[])
        self.collect.side_effect = [inventory(), failed]
        with self.assertRaises(AnalysisDenied) as error:
            gather(options(systemd="both", systemd_state="enabled"), self.reader)
        self.assertEqual(error.exception.reason, "input")
        self.assertEqual(self.collect.call_args_list,
                         [call(scope="system", view="enabled"), call(scope="user", view="enabled")])
        self.assertNotIn("For the answer:", self.output.getvalue())
        self.assertIn("scope: user, status: unavailable", self.output.getvalue())

    def test_interrupted_city_selection_never_requests_current_weather(self):
        self.geocode.return_value = [LOCATION, OTHER]
        for exception in (EOFError, KeyboardInterrupt):
            with self.subTest(exception=exception), self.assertRaises(exception):
                gather(options(weather="Other City"), Mock(side_effect=exception))
        self.current.assert_not_called()
        self.collect.assert_not_called()

    def test_systemd_policy_revoked_during_consent_prevents_source_read(self):
        revoked = [False]
        def consent(*args, **kwargs):
            revoked[0] = True
            return True
        def check():
            if revoked[0]:
                raise assistant_flow.AccessDenied()
        self.confirm.side_effect = consent
        with self.assertRaises(assistant_flow.AccessDenied):
            ask_context._systemd("system", "enabled", options(), self.reader, check=check)
        self.collect.assert_not_called()

    def test_systemd_rechecks_each_scope_before_reading_it(self):
        revoked = [False]
        def collect(**kwargs):
            revoked[0] = True
            return inventory(**kwargs)
        def check():
            if revoked[0]:
                raise assistant_flow.AccessDenied()
        self.collect.side_effect = collect
        with self.assertRaises(assistant_flow.AccessDenied):
            ask_context._systemd("both", "enabled", options(), self.reader, check=check)
        self.collect.assert_called_once_with(scope="system", view="enabled")

    def test_weather_policy_revoked_during_consent_prevents_geocoding(self):
        revoked = [False]
        def consent(*args, **kwargs):
            revoked[0] = True
            return True
        def check():
            if revoked[0]:
                raise assistant_flow.AccessDenied()
        self.confirm.side_effect = consent
        with self.assertRaises(assistant_flow.AccessDenied):
            ask_context._weather("Example City", options(), self.reader, check=check)
        self.geocode.assert_not_called()
        self.current.assert_not_called()

    def test_weather_policy_revoked_at_location_selection_prevents_current_request(self):
        revoked = [False]
        self.geocode.return_value = [LOCATION, OTHER]
        def choose(prompt):
            revoked[0] = True
            return "2"
        def check():
            if revoked[0]:
                raise assistant_flow.AccessDenied()
        with self.assertRaises(assistant_flow.AccessDenied):
            ask_context._weather("Other City", options(), choose, check=check)
        self.geocode.assert_called_once_with("Other City")
        self.current.assert_not_called()


if __name__ == "__main__":
    unittest.main()
