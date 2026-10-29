import datetime

from django import forms
from django.test import SimpleTestCase

from api.core.forms import form_error, post_data


class SampleForm(forms.Form):
    family = forms.CharField()
    name = forms.CharField()

    def clean(self):
        raise forms.ValidationError("nope")


class FormErrorTests(SimpleTestCase):
    def test_fields_and_non_field_errors(self):
        form = SampleForm({})
        form.is_valid()
        error = form_error(form)
        self.assertEqual((error.status, error.code), (422, "validation_error"))
        self.assertEqual(set(error.fields), {"family", "name", "non_field_errors"})

    def test_aliases_rename_fields(self):
        form = SampleForm({})
        form.is_valid()
        self.assertEqual(set(form_error(form, {"family": "family_id"}).fields), {"family_id", "name", "non_field_errors"})

    def test_all_becomes_non_field_errors(self):
        form = SampleForm({"family": "a", "name": "b"})
        form.is_valid()
        self.assertEqual(form_error(form).fields, {"non_field_errors": ["nope"]})


class PostDataTests(SimpleTestCase):
    def test_translation_to_form_post(self):
        when = datetime.datetime(2026, 1, 1, 10, 0)
        data = post_data(
            {"a": None, "ids": [1, 2], "one": 3, "flag": True, "off": False, "when": when, "plain": "x"},
            multiple={"ids": "members"},
            single={"one": "loc"},
            checkboxes={"flag", "off"},
            extra={"teams": ["t"]},
        )
        self.assertEqual(data, {"teams": ["t"], "members": ["1", "2"], "loc": "3", "flag": "on", "when": when.isoformat(), "plain": "x"})
