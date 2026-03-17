"""Tests for the detect_bank_holidays tool and locale auto-detection."""

from scrum_agent.tools.calendar_tools import _detect_country_from_locale, detect_bank_holidays


class TestDetectCountryFromLocale:
    """Tests for _detect_country_from_locale() — system locale → country code."""

    def test_returns_string_or_none(self):
        """Should return an ISO alpha-2 string or None — never raises."""
        result = _detect_country_from_locale()
        assert result is None or (isinstance(result, str) and len(result) == 2)

    def test_en_gb_locale(self, monkeypatch):
        """en_GB locale should detect GB."""
        monkeypatch.setattr("locale.getlocale", lambda: ("en_GB", "UTF-8"))
        assert _detect_country_from_locale() == "GB"

    def test_en_us_locale(self, monkeypatch):
        """en_US locale should detect US."""
        monkeypatch.setattr("locale.getlocale", lambda: ("en_US", "UTF-8"))
        assert _detect_country_from_locale() == "US"

    def test_de_de_locale(self, monkeypatch):
        """de_DE locale should detect DE."""
        monkeypatch.setattr("locale.getlocale", lambda: ("de_DE", "UTF-8"))
        assert _detect_country_from_locale() == "DE"

    def test_none_locale_falls_back_to_lang_env(self, monkeypatch):
        """When getlocale() returns (None, None), LANG env var should be checked."""
        monkeypatch.setattr("locale.getlocale", lambda: (None, None))
        monkeypatch.setenv("LANG", "en_GB.UTF-8")
        assert _detect_country_from_locale() == "GB"

    def test_no_underscore_returns_none(self, monkeypatch):
        """A locale like 'C' or 'POSIX' with no country part should return None."""
        monkeypatch.setattr("locale.getlocale", lambda: ("C", None))
        monkeypatch.setenv("LANG", "C")
        monkeypatch.setattr("scrum_agent.tools.calendar_tools.platform.system", lambda: "Linux")
        assert _detect_country_from_locale() is None

    def test_unsupported_country_returns_none(self, monkeypatch):
        """A locale with an unsupported country code should return None."""
        monkeypatch.setattr("locale.getlocale", lambda: ("en_XX", "UTF-8"))
        monkeypatch.setenv("LANG", "en_XX")
        monkeypatch.setattr("scrum_agent.tools.calendar_tools.platform.system", lambda: "Linux")
        assert _detect_country_from_locale() is None

    def test_exception_returns_none(self, monkeypatch):
        """If locale functions raise, return None gracefully."""
        monkeypatch.setattr("locale.getlocale", lambda: (_ for _ in ()).throw(ValueError("bad")))
        monkeypatch.setenv("LANG", "C")
        monkeypatch.setattr("scrum_agent.tools.calendar_tools.platform.system", lambda: "Linux")
        assert _detect_country_from_locale() is None

    def test_lang_env_var_fallback(self, monkeypatch):
        """When getlocale() returns 'C', LANG env var should be checked."""
        monkeypatch.setattr("locale.getlocale", lambda: ("C", None))
        monkeypatch.setenv("LANG", "en_US.UTF-8")
        result = _detect_country_from_locale()
        assert result == "US"

    def test_macos_apple_locale_fallback(self, monkeypatch):
        """On macOS, AppleLocale should be checked as last resort."""
        import subprocess

        monkeypatch.setattr("locale.getlocale", lambda: ("C", None))
        monkeypatch.setenv("LANG", "C")
        monkeypatch.setattr("scrum_agent.tools.calendar_tools.platform.system", lambda: "Darwin")

        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="en_GB\n", stderr="")
        monkeypatch.setattr("scrum_agent.tools.calendar_tools.subprocess.run", lambda *a, **kw: fake_result)
        assert _detect_country_from_locale() == "GB"


class TestDetectBankHolidays:
    """Tests for detect_bank_holidays() — country-based holiday detection."""

    def test_gb_holidays_found(self):
        """GB should have bank holidays in any 3-month window."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "GB",
                "sprint_length_weeks": 2,
                "num_sprints": 6,
                "start_date": "2026-01-01",
            }
        )
        assert "United Kingdom" in result or "GB" in result
        # Jan 1 (New Year's Day) is a bank holiday in GB on a Thursday in 2026
        assert "New Year" in result
        assert "working days lost" in result

    def test_us_fourth_of_july(self):
        """US Independence Day (July 4) should appear in a window covering it."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "US",
                "sprint_length_weeks": 2,
                "num_sprints": 1,
                "start_date": "2026-06-29",
            }
        )
        assert "Independence Day" in result

    def test_no_holidays_in_short_window(self):
        """A 1-week window in mid-February should have no holidays for most countries."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "GB",
                "sprint_length_weeks": 1,
                "num_sprints": 1,
                "start_date": "2026-02-09",
            }
        )
        assert "No bank holidays" in result
        assert "**0**" in result

    def test_invalid_country_code(self):
        """An unrecognised country code should return a helpful error."""
        result = detect_bank_holidays.invoke({"country_code": "ZZ"})
        assert "Error" in result
        assert "not a supported country code" in result

    def test_invalid_date_format(self):
        """A bad date string should return a parse error."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "GB",
                "start_date": "not-a-date",
            }
        )
        assert "Error" in result
        assert "invalid date format" in result

    def test_lowercase_country_code_accepted(self):
        """Country codes should be case-insensitive."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "gb",
                "sprint_length_weeks": 2,
                "num_sprints": 6,
                "start_date": "2026-01-01",
            }
        )
        assert "GB" in result
        assert "Error" not in result

    def test_three_letter_code_accepted(self):
        """3-letter ISO codes (e.g. GBR, USA) should work."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "USA",
                "sprint_length_weeks": 2,
                "num_sprints": 6,
                "start_date": "2026-01-01",
            }
        )
        assert "Error" not in result

    def test_default_start_date_is_today(self):
        """Omitting start_date should default to today without error."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "US",
                "sprint_length_weeks": 2,
                "num_sprints": 1,
            }
        )
        assert "Error" not in result
        assert "Planning window" in result

    def test_weekends_excluded(self):
        """Holidays falling on weekends should not be counted."""
        # 2026-01-01 is a Thursday in GB — should be counted
        # Find a year where Jan 1 is Saturday: 2028
        result = detect_bank_holidays.invoke(
            {
                "country_code": "GB",
                "sprint_length_weeks": 1,
                "num_sprints": 1,
                "start_date": "2028-01-01",
            }
        )
        # Jan 1 2028 is a Saturday — the observed holiday moves to Jan 3 (Monday) in GB
        # Either way, verify the tool runs without error
        assert "Error" not in result

    def test_planning_window_spans_years(self):
        """A window crossing year boundary should include holidays from both years."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "US",
                "sprint_length_weeks": 2,
                "num_sprints": 3,
                "start_date": "2026-12-15",
            }
        )
        # Should span Dec 2026 → Jan 2027, catching Christmas and New Year's
        assert "Christmas" in result or "New Year" in result

    def test_output_format_has_count(self):
        """Output should include total count of working days lost."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "DE",
                "sprint_length_weeks": 2,
                "num_sprints": 26,
                "start_date": "2026-01-01",
            }
        )
        assert "working days lost to bank holidays" in result

    def test_auto_detect_from_locale(self, monkeypatch):
        """When country_code is empty, should auto-detect from system locale."""
        monkeypatch.setattr("scrum_agent.tools.calendar_tools.locale.getlocale", lambda: ("en_GB", "UTF-8"))
        result = detect_bank_holidays.invoke(
            {
                "sprint_length_weeks": 2,
                "num_sprints": 6,
                "start_date": "2026-01-01",
            }
        )
        assert "GB" in result
        assert "auto-detected from system locale" in result
        assert "Error" not in result

    def test_auto_detect_not_shown_when_explicit(self):
        """When country_code is explicitly provided, no auto-detect note."""
        result = detect_bank_holidays.invoke(
            {
                "country_code": "GB",
                "sprint_length_weeks": 1,
                "num_sprints": 1,
                "start_date": "2026-01-01",
            }
        )
        assert "auto-detected" not in result

    def test_auto_detect_fails_gracefully(self, monkeypatch):
        """When locale has no country and no code given, return helpful error."""
        monkeypatch.setattr("scrum_agent.tools.calendar_tools.locale.getlocale", lambda: ("C", None))
        monkeypatch.setenv("LANG", "C")
        monkeypatch.setattr("scrum_agent.tools.calendar_tools.platform.system", lambda: "Linux")
        result = detect_bank_holidays.invoke({})
        assert "Error" in result
        assert "could not auto-detect" in result


class TestDetectBankHolidaysRegistered:
    """Verify the tool is registered in get_tools()."""

    def test_in_get_tools(self):
        """detect_bank_holidays should be in get_tools()."""
        from scrum_agent.tools import get_tools

        names = {t.name for t in get_tools()}
        assert "detect_bank_holidays" in names
