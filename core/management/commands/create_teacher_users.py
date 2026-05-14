from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Teacher


@dataclass(frozen=True)
class SeedTeacherUser:
    username: str
    email: str
    password: str
    first_name: str
    last_name: str


SEED_TEACHER_USERS: tuple[SeedTeacherUser, ...] = (
    SeedTeacherUser(
        username="teacher_user_1",
        email="teacher.user1@example.com",
        password="ChangeMe123!",
        first_name="Aarav",
        last_name="Mensah",
    ),
    SeedTeacherUser(
        username="teacher_user_2",
        email="teacher.user2@example.com",
        password="ChangeMe123!",
        first_name="Nia",
        last_name="Boateng",
    ),
    SeedTeacherUser(
        username="teacher_user_3",
        email="teacher.user3@example.com",
        password="ChangeMe123!",
        first_name="Kofi",
        last_name="Owusu",
    ),
)


class Command(BaseCommand):
    help = "Create 3 non-admin users and link each to a Teacher profile (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--set-password",
            action="store_true",
            help="Reset passwords for existing users to the values defined in this command.",
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        should_reset_password = options["set_password"]

        self.stdout.write(self.style.NOTICE("Seeding teacher-linked non-admin users..."))

        for seed in SEED_TEACHER_USERS:
            with transaction.atomic():
                user, user_created, user_lookup = self._get_or_create_user(
                    user_model=user_model,
                    seed=seed,
                    should_reset_password=should_reset_password,
                )
                teacher, teacher_created, linked_now = self._get_or_create_and_link_teacher(
                    seed=seed,
                    user=user,
                )

            user_status = "created" if user_created else f"existing ({user_lookup})"
            teacher_status = "created" if teacher_created else "existing"
            link_status = "linked" if linked_now else "already linked"
            self.stdout.write(
                f"- User {user.username} [{user_status}] -> Teacher #{teacher.pk} {teacher} "
                f"[{teacher_status}, {link_status}]"
            )

        self.stdout.write(self.style.SUCCESS("Done. Teacher-linked non-admin users are ready."))

    def _get_or_create_user(self, *, user_model, seed: SeedTeacherUser, should_reset_password: bool):
        user = user_model.objects.filter(username=seed.username).first()
        lookup = "username"

        if user is None:
            user = user_model.objects.filter(email__iexact=seed.email).first()
            lookup = "email"

        created = False
        if user is None:
            user = user_model.objects.create_user(
                username=seed.username,
                email=seed.email,
                password=seed.password,
                first_name=seed.first_name,
                last_name=seed.last_name,
                is_staff=False,
                is_superuser=False,
                is_active=True,
            )
            return user, True, "username"

        update_fields: list[str] = []
        if user.email.lower() != seed.email.lower():
            user.email = seed.email
            update_fields.append("email")
        if user.first_name != seed.first_name:
            user.first_name = seed.first_name
            update_fields.append("first_name")
        if user.last_name != seed.last_name:
            user.last_name = seed.last_name
            update_fields.append("last_name")
        if user.is_staff:
            user.is_staff = False
            update_fields.append("is_staff")
        if user.is_superuser:
            user.is_superuser = False
            update_fields.append("is_superuser")
        if not user.is_active:
            user.is_active = True
            update_fields.append("is_active")

        if should_reset_password:
            user.set_password(seed.password)
            update_fields.append("password")

        if update_fields:
            user.save(update_fields=update_fields)

        return user, created, lookup

    def _get_or_create_and_link_teacher(self, *, seed: SeedTeacherUser, user):
        teacher = Teacher.objects.filter(user=user).first()
        if teacher:
            return teacher, False, False

        teacher, created = Teacher.objects.get_or_create(
            email=seed.email,
            defaults={
                "first_name": seed.first_name,
                "last_name": seed.last_name,
                "user": user,
            },
        )

        linked_now = False
        if teacher.user_id != user.id:
            teacher.user = user
            teacher.save(update_fields=["user", "updated_at"])
            linked_now = True

        return teacher, created, linked_now
