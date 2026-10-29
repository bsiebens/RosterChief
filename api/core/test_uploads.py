from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from api.core.errors import ApiError
from api.core.uploads import IMAGE_TYPES, SPREADSHEET_TYPES, validate_upload


def upload(name="a.png", content=b"x" * 10, content_type="image/png"):
    return SimpleUploadedFile(name, content, content_type=content_type)


class ValidateUploadTests(SimpleTestCase):
    def assertRejected(self, status, code, **kwargs):
        with self.assertRaises(ApiError) as caught:
            validate_upload(**kwargs)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
        return caught.exception

    def test_accepts_a_valid_file_and_returns_it(self):
        file = upload()
        self.assertIs(validate_upload(file), file)

    def test_missing_part_is_422_on_the_field(self):
        error = self.assertRejected(422, "validation_error", upload=None, field="photo")
        self.assertIn("photo", error.fields)

    def test_empty_file_is_422_unless_allowed(self):
        self.assertRejected(422, "validation_error", upload=upload(content=b""))
        validate_upload(upload(content=b""), allow_empty=True)

    def test_too_large_is_413(self):
        self.assertRejected(413, "file_too_large", upload=upload(content=b"x" * 11), max_bytes=10)
        validate_upload(upload(content=b"x" * 10), max_bytes=10)

    @override_settings(API_UPLOAD_MAX_BYTES=5)
    def test_default_limit_comes_from_settings(self):
        self.assertRejected(413, "file_too_large", upload=upload())

    def test_wrong_content_type_is_415(self):
        self.assertRejected(415, "unsupported_media_type", upload=upload("a.pdf", content_type="application/pdf"))

    def test_content_type_parameters_and_case_are_ignored(self):
        validate_upload(upload(content_type="IMAGE/PNG; charset=binary"), content_types=IMAGE_TYPES)

    def test_per_call_types_and_any_type(self):
        sheet = upload("m.xlsx", content_type=SPREADSHEET_TYPES[0])
        validate_upload(sheet, content_types=SPREADSHEET_TYPES)
        validate_upload(upload("a.pdf", content_type="application/pdf"), content_types=())

    def test_extension_rule(self):
        sheet = SPREADSHEET_TYPES
        validate_upload(upload("M.XLSX", content_type=sheet[0]), content_types=sheet, extensions=(".xlsx",))
        self.assertRejected(415, "unsupported_media_type", upload=upload("m.csv", content_type=sheet[0]), content_types=sheet, extensions=(".xlsx",))

    def test_generic_ok_waives_the_type_for_a_matching_extension_only(self):
        kwargs = {"content_types": SPREADSHEET_TYPES, "extensions": (".xlsx",)}
        validate_upload(upload("m.xlsx", content_type="application/octet-stream"), generic_ok=True, **kwargs)
        validate_upload(upload("m.xlsx", content_type=""), generic_ok=True, **kwargs)
        self.assertRejected(415, "unsupported_media_type", upload=upload("m.xlsx", content_type="application/octet-stream"), **kwargs)
        self.assertRejected(415, "unsupported_media_type", upload=upload("m.csv", content_type="application/octet-stream"), generic_ok=True, **kwargs)
        self.assertRejected(415, "unsupported_media_type", upload=upload("m.xlsx", content_type="text/csv"), generic_ok=True, **kwargs)
