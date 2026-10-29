from api.core.errors import ApiError
from api.core.jobs import JobContext, job_task, start_job
from api.models import Job
from api.protected.tests import CLUB_HOST, AppApiTestCase
from club.models import ClubRole
from rosterchief.test_support import make_club


@job_task(name="api.test-ok")
def ok_job(ctx: JobContext, count):
    for i in range(count):
        ctx.progress(i + 1, count)
    return {"count": count, "user": ctx.user.email}


@job_task(name="api.test-api-error")
def api_error_job(ctx):
    raise ApiError(409, "already_sent", "Already sent.")


@job_task(name="api.test-crash")
def crash_job(ctx):
    raise RuntimeError("secret detail")


class FakeRequest:
    def __init__(self, user, club):
        self.user, self.club = user, club


class JobTests(AppApiTestCase):
    def start(self, task, *args):
        with self.captureOnCommitCallbacks(execute=True):
            return start_job(FakeRequest(self.user, self.club), task, *args)

    def token(self):
        ClubRole.objects.update_or_create(club=self.club, member=self.member, defaults={"role": ClubRole.Roles.ADMIN})
        return self.login_with_totp()

    def get(self, job_id, token, host=CLUB_HOST):
        return self.api("get", f"/manage/jobs/{job_id}", token=token, host=host)

    def test_start_returns_pending_row_and_task_runs_on_commit(self):
        out = self.start(ok_job, 4)
        self.assertEqual(out["status"], "pending")
        job = Job.objects.get(pk=out["id"])
        self.assertEqual((job.club, job.user, job.name), (self.club, self.user, "api.test-ok"))
        self.assertEqual(job.status, Job.Status.SUCCEEDED)
        self.assertEqual(job.progress, 100)

    def test_not_run_before_commit(self):
        out = start_job(FakeRequest(self.user, self.club), ok_job, 1)
        self.assertEqual(Job.objects.get(pk=out["id"]).status, Job.Status.PENDING)

    def test_poll_succeeded(self):
        out = self.start(ok_job, 2)
        body = self.get(out["id"], self.token()).json()
        self.assertEqual(body["status"], "succeeded")
        self.assertEqual(body["progress"], 100)
        self.assertEqual(body["result"], {"count": 2, "user": self.user.email})
        self.assertIsNone(body["error"])

    def test_api_error_keeps_code_and_message(self):
        body = self.get(self.start(api_error_job)["id"], self.token()).json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"], {"code": "already_sent", "message": "Already sent."})
        self.assertIsNone(body["result"])

    def test_crash_is_generic_and_does_not_leak(self):
        with self.assertLogs("api.core.jobs", level="ERROR"):
            body = self.get(self.start(crash_job)["id"], self.token()).json()
        self.assertEqual(body["status"], "failed")
        self.assertEqual(body["error"]["code"], "job_failed")
        self.assertNotIn("secret", body["error"]["message"])

    def test_requires_token(self):
        out = self.start(ok_job, 1)
        self.assertError(self.get(out["id"], None), 401, "unauthenticated")

    def test_other_user_gets_404(self):
        out = self.start(ok_job, 1)
        Job.objects.filter(pk=out["id"]).update(user=self.make_other_user())
        self.assertError(self.get(out["id"], self.token()), 404, "not_found")

    def test_other_club_gets_404(self):
        out = self.start(ok_job, 1)
        other = make_club(name="Rival FC", slug="rival-fc")
        Job.objects.filter(pk=out["id"]).update(club=other)
        self.assertError(self.get(out["id"], self.token()), 404, "not_found")

    def test_unknown_id_is_404(self):
        self.assertError(self.get("6f1c3a52-0000-4000-8000-000000000000", self.token()), 404, "not_found")

    def make_other_user(self):
        from django.contrib.auth import get_user_model

        return get_user_model().objects.create_user(email="other@example.com", password="x")
