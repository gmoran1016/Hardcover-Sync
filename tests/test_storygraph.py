import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import TimeoutException
from selenium.webdriver.common.by import By

import storygraph
from storygraph import StorygraphSync


BOOK = {"title": "Winter's Heart", "author": "Robert Jordan"}
BOOK_URL = "https://app.thestorygraph.com/books/winters-heart"


class FakeElement:
    def __init__(self, text: str, displayed: bool = True):
        self.text = text
        self._displayed = displayed

    def is_displayed(self):
        return self._displayed


class FakeDriver:
    def __init__(self, status: str, finish_buttons: list[FakeElement]):
        self.status_labels = [FakeElement(status)]
        self.finish_buttons = finish_buttons
        self.clicked = []
        self.current_url = ""

    def get(self, url):
        self.current_url = url

    def find_elements(self, by, selector):
        if (by, selector) == (By.CSS_SELECTOR, ".read-status-label"):
            return self.status_labels
        if (by, selector) == (
            By.XPATH,
            '//button[normalize-space(.)="Dismiss"]',
        ):
            return []
        if (by, selector) == (By.CSS_SELECTOR, ".expand-dropdown-button"):
            return [FakeElement("expand")]
        if (by, selector) == (By.CSS_SELECTOR, ".mark-as-finished-btn"):
            return self.finish_buttons
        return []

    def execute_script(self, _script, *arguments):
        if arguments:
            self.clicked.append(arguments[0].text)


class FakeProgressControl:
    def __init__(self, value="", displayed=True, valid=True):
        self.value = value
        self._displayed = displayed
        self.valid = valid
        self.selected_units = []
        self.form = None
        self.min = "12"
        self.max = "100"
        self.validation_message = ""

    def is_displayed(self):
        return self._displayed

    def find_element(self, by, selector):
        if (by, selector) == (By.XPATH, "./ancestor::form[1]"):
            return self.form
        raise AssertionError((by, selector))

    def get_attribute(self, name):
        return {
            "value": self.value,
            "min": self.min,
            "max": self.max,
        }.get(name, "")

    def clear(self):
        self.value = ""

    def send_keys(self, value):
        self.value += str(value)

    def select(self, unit):
        self.value = unit
        self.selected_units.append(unit)
        if unit == "pages":
            self.form.number.min = self.form.last_pages.value
            self.form.number.max = self.form.book_pages.value
        else:
            self.form.number.min = self.form.last_percent.value
            self.form.number.max = "100"


class FakeSaveControl(FakeProgressControl):
    def __init__(self, form, updates_saved_state=True):
        super().__init__("Save")
        self.form = form
        self.clicks = 0
        self.updates_saved_state = updates_saved_state

    def click(self):
        self.clicks += 1
        if not self.updates_saved_state:
            return
        if self.form.unit.value == "pages":
            self.form.last_pages.value = self.form.number.value
        else:
            self.form.last_percent.value = self.form.number.value


class FakeProgressForm:
    def __init__(self, displayed, valid=True, updates_saved_state=True):
        self.number = FakeProgressControl(displayed=displayed, valid=valid)
        self.unit = FakeProgressControl("percentage", displayed=displayed)
        self.last_pages = FakeProgressControl("75", displayed=False)
        self.book_pages = FakeProgressControl("624", displayed=False)
        self.last_percent = FakeProgressControl("12", displayed=False)
        self.save = FakeSaveControl(self, updates_saved_state)
        for control in (
            self.number,
            self.unit,
            self.last_pages,
            self.book_pages,
            self.last_percent,
            self.save,
        ):
            control.form = self
        if not valid:
            self.number.validation_message = (
                "Value must be within the active unit range."
            )
        self.valid = valid

    def find_element(self, by, selector):
        if by != By.CSS_SELECTOR:
            raise AssertionError((by, selector))
        controls = {
            "select#read_status_progress_type": self.unit,
            "input.progress-tracker-update-button": self.save,
            ".read-status-last-reached-pages": self.last_pages,
            ".read-status-book-num-of-pages": self.book_pages,
            ".read-status-last-reached-percent": self.last_percent,
        }
        return controls[selector]


class FakeProgressDriver:
    def __init__(self, visible_form, hidden_form=None):
        self.visible_form = visible_form
        self.hidden_form = hidden_form or FakeProgressForm(False)

    def find_elements(self, by, selector):
        if (by, selector) == (
            By.XPATH,
            '//button[normalize-space(.)="Dismiss"]',
        ):
            return []
        if (by, selector) == (
            By.CSS_SELECTOR,
            "input#read_status_progress_number",
        ):
            return [self.hidden_form.number, self.visible_form.number]
        return []

    def execute_script(self, script, *arguments):
        if "checkValidity" in script:
            return arguments[0].valid
        if "validationMessage" in script:
            return arguments[0].validation_message
        if arguments:
            return "visible"
        return None


class FakeSelect:
    def __init__(self, element):
        self.element = element

    def select_by_value(self, value):
        self.element.select(value)


class ImmediateWait:
    def __init__(self, driver, _timeout):
        self.driver = driver

    def until(self, condition):
        result = condition(self.driver)
        if not result:
            raise TimeoutException()
        return result


class StorygraphTests(unittest.TestCase):
    def run_finish(self, driver):
        sync = StorygraphSync("", "")
        sync.driver = driver
        wait = Mock()
        wait.until.return_value = True
        with (
            patch.object(storygraph.time, "sleep"),
            patch.object(storygraph, "WebDriverWait", return_value=wait),
        ):
            return sync.mark_finished(BOOK, BOOK_URL)

    def test_current_reading_status_ignores_hidden_labels(self):
        driver = FakeDriver("read", [])
        driver.status_labels = [
            FakeElement("read", displayed=False),
            FakeElement("  ReReading  "),
        ]
        self.assertEqual(storygraph.current_reading_status(driver), "rereading")

    def test_mark_finished_succeeds_without_click_when_status_is_read(self):
        driver = FakeDriver("read", [])
        result = self.run_finish(driver)
        self.assertTrue(result.success)
        self.assertEqual(result.target_url, BOOK_URL)
        self.assertEqual(driver.clicked, [])

    def test_mark_finished_clicks_action_for_currently_reading(self):
        driver = FakeDriver("currently reading", [FakeElement("finished")])
        result = self.run_finish(driver)
        self.assertTrue(result.success)
        self.assertIn("finished", driver.clicked)

    def test_mark_finished_fails_when_not_read_and_action_is_missing(self):
        driver = FakeDriver("currently reading", [])
        result = self.run_finish(driver)
        self.assertFalse(result.success)

    def run_progress(self, form, pages=103, pct=12.4):
        hidden = FakeProgressForm(False)
        driver = FakeProgressDriver(form, hidden)
        sync = StorygraphSync("", "")
        sync.driver = driver
        track = FakeElement("Edit your progress")
        with (
            patch.object(storygraph.time, "sleep"),
            patch.object(storygraph, "WebDriverWait", ImmediateWait),
            patch.object(storygraph, "Select", FakeSelect, create=True),
            patch.object(sync, "_find_track_progress_button", return_value=track),
        ):
            result = sync._do_update_progress(pages, pct, 832)
        return result, hidden

    def test_update_progress_selects_pages_before_entering_page_count(self):
        form = FakeProgressForm(True)
        result, _hidden = self.run_progress(form)
        self.assertTrue(result)
        self.assertEqual(form.unit.selected_units, ["pages"])
        self.assertEqual(form.number.value, "103")
        self.assertEqual(form.number.max, "624")
        self.assertEqual(form.last_pages.value, "103")
        self.assertEqual(form.save.clicks, 1)

    def test_update_progress_selects_percentage_for_percentage_only_progress(self):
        form = FakeProgressForm(True)
        result, _hidden = self.run_progress(form, pages=None, pct=12.4)
        self.assertTrue(result)
        self.assertEqual(form.unit.selected_units, ["percentage"])
        self.assertEqual(form.number.value, "12")
        self.assertEqual(form.last_percent.value, "12")

    def test_update_progress_uses_controls_from_visible_form(self):
        form = FakeProgressForm(True)
        result, hidden = self.run_progress(form)
        self.assertTrue(result)
        self.assertEqual(hidden.unit.selected_units, [])
        self.assertEqual(hidden.save.clicks, 0)

    def test_update_progress_rejects_invalid_form_without_submitting(self):
        form = FakeProgressForm(True, valid=False)
        result, _hidden = self.run_progress(form)
        self.assertFalse(result)
        self.assertEqual(form.save.clicks, 0)

    def test_update_progress_fails_when_saved_state_does_not_change(self):
        form = FakeProgressForm(True, updates_saved_state=False)
        result, _hidden = self.run_progress(form)
        self.assertFalse(result)
        self.assertEqual(form.save.clicks, 1)


if __name__ == "__main__":
    unittest.main()
