"""Wipe and reseed one club (the "demo" club by default) with realistic data
covering the app's major features, for running product demos.

Wiping goes through controlpanel.services.club_deletion.delete_club -- the
same PROTECT-resolution logic the control panel's own "danger zone" hard
delete uses -- then recreates the Club row with the exact same field values
(including its primary key), so every setting/branding choice on it survives
untouched and nothing outside the club can notice the swap. Member/User are
never touched by that delete (see that module's own docstring): only the
given club's own ClubMembership/ClubRole/etc. rows disappear, so the
platform-admin account named by --admin-email keeps existing globally and is
simply re-attached to the fresh club as its ADMIN afterwards.

Every login this command creates gets the same fixed demo password (printed
at the end, alongside every email) -- one password to remember beats a
different throwaway one per person for a demo nobody is meant to keep using
afterwards. The admin account's own password is never touched.
"""

import datetime
from decimal import Decimal

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.utils import timezone
from waffle import get_waffle_flag_model

from club.models import Club, ClubMembership, ClubRole, FeePayment, Season
from club.services.fees import mark_as_paid
from club.services.fees import record_payment as record_fee_payment
from club.services.seasons import generate_seasons
from controlpanel.services.club_deletion import ClubDeletionBlocked, delete_club
from events.models import Attendance, Event, EventReferee, Location, Opponent
from features.commands import CommandError, MaintenanceAwareCommand
from members.models import FamilyMembership, Member
from members.services.family import add_child_to_family, add_parent_to_family, get_or_create_login_member, get_or_create_login_user, register_family
from news.models import News
from shop.models import Order, OrderLine, Payment, Product
from shop.services.payments import record_payment as record_shop_payment
from teams.models import OfficialLevel, OfficialProfile, Position, RefereeLevel, RefereeProfile, StaffAssignment, Team, TeamMembership

User = get_user_model()

DEMO_PASSWORD = "RosterDemo2026!"


class Command(MaintenanceAwareCommand):
    help = "Wipes a club's data (keeping its own settings and one named admin) and reseeds it with realistic demo data."

    def add_arguments(self, parser):
        parser.add_argument("--club", default="demo", help="Slug of the club to wipe and reseed. Default: demo.")
        parser.add_argument("--admin-email", default="bernard@siebens.org", help="Email of the account to keep as this club's ADMIN. Must already exist. Default: bernard@siebens.org.")

    def handle(self, *args, **options):
        club_slug = options["club"]
        admin_email = options["admin_email"]

        try:
            club = Club.objects.get(slug=club_slug)
        except Club.DoesNotExist:
            raise CommandError(f"No club with slug {club_slug!r} exists.") from None

        admin_user = User.objects.filter(email__iexact=admin_email).first()
        admin_member = Member.objects.filter(user=admin_user).first() if admin_user else None
        if admin_member is None:
            raise CommandError(f"{admin_email!r} has no account with a Member record -- refusing to wipe {club} without a known admin to keep.")

        self.credentials = []  # [(email, note), ...], printed at the end.

        self.stdout.write(f"Wiping {club}...")
        club = self._wipe(club)

        # A login-less child/filler-player Member outlives the wipe above
        # (Member is global, never club-scoped) -- once its ClubMembership/
        # FamilyMembership are gone too (as they just were), it's inert:
        # unreachable from any club's UI, on no roster, in no family. Cleaned
        # up here, right after wiping and before reseeding creates this run's
        # own fresh set, so a previous run's "Riley Anderson" never lingers
        # even one run longer than it has to.
        orphaned = Member.objects.filter(user__isnull=True, member_of__isnull=True, family_memberships__isnull=True)
        orphaned_count = orphaned.count()
        orphaned.delete()
        if orphaned_count:
            self.stdout.write(f"Removed {orphaned_count} orphaned, login-less Member row(s) left over from a previous run.")

        self.stdout.write("Reseeding...")
        seasons = self._make_seasons(club)
        self._reattach_admin(club, admin_member, seasons["current"])
        self._seed(club, seasons, admin_member)

        self.stdout.write(self.style.SUCCESS(f"\n{club} has been wiped and reseeded."))
        self.stdout.write(f"\nEvery seeded login shares one password: {DEMO_PASSWORD}\n")
        self.stdout.write("Demo logins:")
        self.stdout.write(f"  {admin_email:<40} admin (your own password, unchanged)")
        for email, note in self.credentials:
            self.stdout.write(f"  {email:<40} {note}")

    # -- Wipe -------------------------------------------------------------

    def _wipe(self, club: Club) -> Club:
        """Deletes every row the club owns, then recreates the Club row itself
        from a snapshot of its own fields -- see this module's own docstring."""
        field_names = [field.name for field in Club._meta.concrete_fields]
        snapshot = {name: getattr(club, name) for name in field_names}
        try:
            delete_club(club)
        except ClubDeletionBlocked as error:
            raise CommandError(str(error)) from error
        return Club.objects.create(**snapshot)

    # -- Seasons ------------------------------------------------------------

    def _make_seasons(self, club: Club) -> dict:
        today = timezone.localdate()
        current = generate_seasons(club, until=today)[0]
        previous_start = current.start_date - relativedelta(months=club.season_duration_months)
        previous_end = current.start_date - datetime.timedelta(days=1)
        previous = Season.objects.create(club=club, start_date=previous_start, end_date=previous_end)
        return {"current": current, "previous": previous}

    def _reattach_admin(self, club: Club, admin_member: Member, current_season: Season):
        # An active ClubMembership auto-grants a MEMBER ClubRole (club/signals.py) --
        # update_or_create, not create, to promote that row to ADMIN rather than
        # collide with it.
        ClubMembership.objects.create(club=club, member=admin_member, season=current_season, kind=ClubMembership.Kind.MEMBER, status=ClubMembership.StatusChoices.ACTIVE, fee_status=ClubMembership.FeeStatus.PAID, signed_up_at=timezone.localdate())
        ClubRole.objects.update_or_create(club=club, member=admin_member, defaults={"role": ClubRole.Roles.ADMIN})

    # -- Login helper ---------------------------------------------------------

    def _seed_login(self, email: str, note: str):
        """Pre-creates (or finds) the User behind ``email`` with the shared demo
        password already set, and records it for the final printout -- called
        before handing ``email`` to register_family/add_parent_to_family/
        get_or_create_login_member, none of which touch a password that's
        already usable."""
        user, _created = get_or_create_login_user(email)
        user.set_password(DEMO_PASSWORD)
        user.save(update_fields=["password"])
        self.credentials.append((email, note))

    # -- Seeding --------------------------------------------------------------

    def _seed(self, club: Club, seasons: dict, admin_member: Member):
        current = seasons["current"]
        previous = seasons["previous"]

        positions = self._make_positions(club)
        teams = self._make_teams(club)
        self._make_referee_and_official_levels(club, teams)
        coaches = self._make_coaches(club, current, teams, positions)
        purchaser = self._make_families(club, current, previous)
        referees = self._make_referees(club, current)
        self._fill_rosters(club, current, teams, positions)
        location, opponents = self._make_locations_and_opponents(club)
        # Assigning teams to a home game fires the real referee/official invite
        # notifications (events/signals.py) -- locmem, not console, so seeding
        # doesn't dump raw MIME to stdout; nothing else needs these to actually
        # be delivered.
        with override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend"):
            self._make_events(club, current, teams, location, opponents, referees, coaches)
        self._make_news(club, admin_member)
        self._make_shop_order(club, current, purchaser)

    def _make_positions(self, club: Club) -> dict:
        return {
            "forward": Position.objects.create(club=club, name="Forward", short_name="F", ordering=1),
            "defense": Position.objects.create(club=club, name="Defense", short_name="D", ordering=2),
            "goalkeeper": Position.objects.create(club=club, name="Goalkeeper", short_name="G", ordering=3),
            "head_coach": Position.objects.create(club=club, name="Head Coach", short_name="HC", ordering=1, staff_position=True, management_position=True),
        }

    def _make_teams(self, club: Club) -> dict:
        u10 = Team.objects.create(club=club, name="U10", short_name="U10", age_min=9, age_max=10)
        u12 = Team.objects.create(club=club, name="U12", short_name="U12", age_min=11, age_max=12)
        u14 = Team.objects.create(club=club, name="U14", short_name="U14", age_min=13, age_max=14)
        u10.feeds_into = u12
        u10.save(update_fields=["feeds_into"])
        u12.feeds_into = u14
        u12.save(update_fields=["feeds_into"])
        return {"u10": u10, "u12": u12, "u14": u14}

    def _make_referee_and_official_levels(self, club: Club, teams: dict):
        regional = RefereeLevel.objects.create(club=club, name="Regional")
        regional.teams.set([teams["u10"], teams["u12"]])
        national = RefereeLevel.objects.create(club=club, name="National", inherits_from=regional)
        national.teams.set([teams["u14"]])
        self.referee_levels = {"regional": regional, "national": national}

        get_waffle_flag_model().objects.get_or_create(name="officials")[0].clubs.add(club)
        table_official = OfficialLevel.objects.create(club=club, name="Table Official")
        table_official.teams.set([teams["u10"], teams["u12"], teams["u14"]])
        self.official_levels = {"table_official": table_official}

    def _make_coaches(self, club: Club, season: Season, teams: dict, positions: dict) -> dict:
        self._seed_login("coach.martinez@demo.rosterchief.app", "head coach, U10 + U12")
        coach_a = get_or_create_login_member("coach.martinez@demo.rosterchief.app", "Alex", "Martinez")
        ClubMembership.objects.create(club=club, member=coach_a, season=season, kind=ClubMembership.Kind.GUARDIAN, status=ClubMembership.StatusChoices.ACTIVE)
        StaffAssignment.objects.create(team=teams["u10"], member=coach_a, season=season, position=positions["head_coach"])
        StaffAssignment.objects.create(team=teams["u12"], member=coach_a, season=season, position=positions["head_coach"])

        self._seed_login("coach.dubois@demo.rosterchief.app", "head coach, U14 -- also a table official")
        coach_b = get_or_create_login_member("coach.dubois@demo.rosterchief.app", "Sam", "Dubois")
        ClubMembership.objects.create(club=club, member=coach_b, season=season, kind=ClubMembership.Kind.GUARDIAN, status=ClubMembership.StatusChoices.ACTIVE)
        StaffAssignment.objects.create(team=teams["u14"], member=coach_b, season=season, position=positions["head_coach"])
        OfficialProfile.objects.create(club=club, member=coach_b, level=self.official_levels["table_official"], valid_until=timezone.localdate() + datetime.timedelta(days=300))

        return {"u10_u12": coach_a, "u14": coach_b}

    def _only_child(self, family) -> Member:
        """The one child on ``family`` right after register_family creates it --
        precise even with same-named leftovers elsewhere, since it's scoped to
        this exact, just-created Family row rather than a global name lookup."""
        return FamilyMembership.objects.get(family=family, role=FamilyMembership.FamilyRole.CHILD).member

    def _make_families(self, club: Club, current: Season, previous: Season) -> Member:
        """Seeds the four family/membership stories, and returns the member who
        goes on to place the demo shop order."""
        # The Andersons: two parents, two active kids, dues settled.
        self._seed_login("anderson.parent@demo.rosterchief.app", "parent, the Andersons -- dues paid")
        family = register_family(
            club,
            current,
            parent_email="anderson.parent@demo.rosterchief.app",
            parent_first_name="Jordan",
            parent_last_name="Anderson",
            child_first_name="Riley",
            child_last_name="Anderson",
            child_date_of_birth=datetime.date(current.start_date.year - 11, 4, 12),
        )
        purchaser = Member.objects.get(user__email__iexact="anderson.parent@demo.rosterchief.app")
        riley = self._only_child(family)

        second_kid = add_child_to_family(club, current, family, first_name="Casey", last_name="Anderson", date_of_birth=datetime.date(current.start_date.year - 9, 7, 3))
        self._seed_login("anderson.parent2@demo.rosterchief.app", "parent, the Andersons")
        add_parent_to_family(club, current, family, email="anderson.parent2@demo.rosterchief.app", first_name="Morgan", last_name="Anderson")

        u12 = Team.objects.get(club=club, short_name="U12")
        u10 = Team.objects.get(club=club, short_name="U10")
        forward = Position.objects.get(club=club, name="Forward")
        defense = Position.objects.get(club=club, name="Defense")
        TeamMembership.objects.create(team=u12, member=riley, season=current, position=forward, jersey_number=9)
        TeamMembership.objects.create(team=u10, member=second_kid, season=current, position=defense, jersey_number=4)
        for kid in (riley, second_kid):
            membership = ClubMembership.objects.get(club=club, member=kid, season=current)
            membership.fee_amount = Decimal("250.00")
            membership.save(update_fields=["fee_amount"])
            mark_as_paid(membership)

        # The Bakkers: a fresh sign-up still sitting in the queue.
        self._seed_login("bakker.parent@demo.rosterchief.app", "parent, the Bakkers -- fresh sign-up, pending approval")
        register_family(
            club,
            current,
            parent_email="bakker.parent@demo.rosterchief.app",
            parent_first_name="Robin",
            parent_last_name="Bakker",
            child_first_name="Finley",
            child_last_name="Bakker",
            child_date_of_birth=datetime.date(current.start_date.year - 10, 2, 20),
            child_status=ClubMembership.StatusChoices.PENDING,
        )

        # The Chens: active last season, never renewed -- needs a follow-up.
        self._seed_login("chen.parent@demo.rosterchief.app", "parent, the Chens -- lapsed last season, not yet renewed")
        chen_family = register_family(
            club,
            previous,
            parent_email="chen.parent@demo.rosterchief.app",
            parent_first_name="Taylor",
            parent_last_name="Chen",
            child_first_name="Avery",
            child_last_name="Chen",
            child_date_of_birth=datetime.date(previous.start_date.year - 11, 9, 30),
        )
        chen_kid = self._only_child(chen_family)
        ClubMembership.objects.filter(club=club, member=chen_kid, season=previous).update(status=ClubMembership.StatusChoices.LAPSED)

        # A standalone adult, no family -- partially paid.
        self._seed_login("veteran.player@demo.rosterchief.app", "standalone member -- dues partially paid")
        veteran = get_or_create_login_member("veteran.player@demo.rosterchief.app", "Drew", "Novak")
        membership = ClubMembership.objects.create(club=club, member=veteran, season=current, kind=ClubMembership.Kind.MEMBER, status=ClubMembership.StatusChoices.ACTIVE, fee_amount=Decimal("250.00"), signed_up_at=timezone.localdate())
        record_fee_payment(membership, amount=Decimal("100.00"), method=FeePayment.Method.BANK_TRANSFER)

        return purchaser

    def _make_referees(self, club: Club, season: Season) -> dict:
        self._seed_login("referee.national@demo.rosterchief.app", "referee -- National level, valid")
        national_ref = get_or_create_login_member("referee.national@demo.rosterchief.app", "Chris", "Okafor")
        ClubMembership.objects.create(club=club, member=national_ref, season=season, kind=ClubMembership.Kind.GUARDIAN, status=ClubMembership.StatusChoices.ACTIVE)
        RefereeProfile.objects.create(club=club, member=national_ref, level=self.referee_levels["national"], valid_until=timezone.localdate() + datetime.timedelta(days=300))

        self._seed_login("referee.expiring@demo.rosterchief.app", "referee -- Regional level, expiring soon")
        expiring_ref = get_or_create_login_member("referee.expiring@demo.rosterchief.app", "Pat", "Larsen")
        ClubMembership.objects.create(club=club, member=expiring_ref, season=season, kind=ClubMembership.Kind.GUARDIAN, status=ClubMembership.StatusChoices.ACTIVE)
        RefereeProfile.objects.create(club=club, member=expiring_ref, level=self.referee_levels["regional"], valid_until=timezone.localdate() + datetime.timedelta(days=20))

        self._seed_login("referee.expired@demo.rosterchief.app", "referee -- Regional level, expired")
        expired_ref = get_or_create_login_member("referee.expired@demo.rosterchief.app", "Jamie", "Kowalski")
        ClubMembership.objects.create(club=club, member=expired_ref, season=season, kind=ClubMembership.Kind.GUARDIAN, status=ClubMembership.StatusChoices.ACTIVE)
        RefereeProfile.objects.create(club=club, member=expired_ref, level=self.referee_levels["regional"], valid_until=timezone.localdate() - datetime.timedelta(days=10))

        return {"national": national_ref, "expiring": expiring_ref, "expired": expired_ref}

    def _fill_rosters(self, club: Club, season: Season, teams: dict, positions: dict):
        filler_names = [
            ("Skyler", "Moreau"), ("Reese", "Haddad"), ("Quinn", "Ibrahim"), ("Rowan", "Petit"),
            ("Emerson", "Vance"), ("Blake", "Torres"), ("Sasha", "Novotny"), ("Charlie", "Lindgren"),
        ]
        team_cycle = [teams["u10"], teams["u12"], teams["u14"]]
        position_cycle = [positions["forward"], positions["defense"], positions["goalkeeper"]]
        jersey = {team.pk: 10 for team in team_cycle}

        for index, (first_name, last_name) in enumerate(filler_names):
            team = team_cycle[index % len(team_cycle)]
            age = 10 if team is teams["u10"] else (12 if team is teams["u12"] else 14)
            member = Member.objects.create(first_name=first_name, last_name=last_name, date_of_birth=datetime.date(season.start_date.year - age, 1 + index % 12, 1 + index % 28))
            ClubMembership.objects.create(
                club=club,
                member=member,
                season=season,
                kind=ClubMembership.Kind.MEMBER,
                status=ClubMembership.StatusChoices.ACTIVE,
                fee_status=ClubMembership.FeeStatus.PAID,
                fee_amount=Decimal("250.00"),
                amount_paid=Decimal("250.00"),
                signed_up_at=timezone.localdate(),
            )
            TeamMembership.objects.create(team=team, member=member, season=season, position=position_cycle[index % len(position_cycle)], jersey_number=jersey[team.pk])
            jersey[team.pk] += 1

    def _make_locations_and_opponents(self, club: Club):
        home = Location.objects.create(club=club, name=f"{club.name} Rink", address="1 Rink Lane", city="Antwerp", zip_code="2000", country="BE", is_home=True)
        opponents = [
            Opponent.objects.create(club=club, name="Ghent Griffins"),
            Opponent.objects.create(club=club, name="Bruges Barons"),
        ]
        return home, opponents

    def _make_events(self, club: Club, season: Season, teams: dict, location: Location, opponents: list, referees: dict, coaches: dict):
        now = timezone.localtime()
        u12_roster = list(Member.objects.filter(team_memberships__team=teams["u12"], team_memberships__season=season))

        past_start = (now - datetime.timedelta(days=5)).replace(hour=14, minute=0, second=0, microsecond=0)
        past_game = Event.objects.create(club=club, kind=Event.EventKind.GAME, title=f"{teams['u12']} vs {opponents[0]}", season=season, location=location, opponent=opponents[0], start=past_start)
        past_game.teams.add(teams["u12"])
        EventReferee.objects.create(event=past_game, member=referees["national"], assigned_by=coaches["u10_u12"])
        for index, member in enumerate(u12_roster):
            status = Attendance.AttendanceStatus.ABSENT if index % 4 == 0 else Attendance.AttendanceStatus.PRESENT
            Attendance.objects.create(event=past_game, member=member, status=status, showed_up=(status == Attendance.AttendanceStatus.PRESENT))

        upcoming_start = (now + datetime.timedelta(days=5)).replace(hour=14, minute=0, second=0, microsecond=0)
        upcoming_game = Event.objects.create(club=club, kind=Event.EventKind.GAME, title=f"{teams['u12']} vs {opponents[1]}", season=season, location=location, opponent=opponents[1], start=upcoming_start)
        upcoming_game.teams.add(teams["u12"])
        # Left with no Attendance rows at all -- shows up needing RSVP, unlike past_game above.

        for offset in (-7, 2, 9):
            training_start = (now + datetime.timedelta(days=offset)).replace(hour=18, minute=30, second=0, microsecond=0)
            training = Event.objects.create(club=club, kind=Event.EventKind.TRAINING, title="Training", season=season, location=location, start=training_start)
            training.teams.add(teams["u10"], teams["u12"])

    def _make_news(self, club: Club, admin_member: Member):
        News.objects.create(
            club=club,
            title="Season kickoff this weekend!",
            body="We're excited to get the new season underway. Come cheer on U10, U12, and U14 this Saturday -- see the calendar for times.",
            visibility=News.Visibility.BOTH,
            status=News.Status.PUBLISHED,
            published_at=timezone.now() - datetime.timedelta(days=2),
            created_by=admin_member,
        )

    def _make_shop_order(self, club: Club, season: Season, purchaser: Member):
        # Open, not whatever the pre-wipe club had -- a demo should be able to
        # walk through the mobile checkout flow live, not just see a closed sign.
        club.shop_open = True
        club.save(update_fields=["shop_open"])

        product = Product.objects.create(club=club, name="Club Hoodie", product_type=Product.ProductType.MERCHANDISE, price=Decimal("45.00"), season=season, is_active=True, is_public=True)
        order = Order.objects.create(club=club, purchaser=purchaser, total=Decimal("45.00"))
        OrderLine.objects.create(order=order, product=product, quantity=1, unit_price=Decimal("45.00"), line_total=Decimal("45.00"))
        record_shop_payment(order, amount=Decimal("45.00"), method=Payment.PaymentMethod.CASH)
        order.fulfillment_status = Order.FulfillmentStatus.DELIVERED
        order.save(update_fields=["fulfillment_status"])
