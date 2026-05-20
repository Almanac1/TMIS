from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from core.models import Contact, Course, Enrollment, Prospect, Student

TM_CODES = {"TMA", "TMC", "TMF", "TMS", "TMWOW", "TM-AD", "TM-CP", "TM-FM", "TM-ST", "TM-WW"}

COURSE_PREREQUISITES = {
    "AT1": ["TM"],
    "AT2": ["TM", "AT1"],
    "AT3": ["TM", "AT1", "AT2"],
    "AT4": ["TM", "AT1", "AT2", "AT3"],
    "AL": ["TM", "AT1", "AT2", "AT3", "AT4"],
}

COURSE_LABELS = {
    "TM": "Transcendental Meditation",
    "AT1": "Advanced Technique 1",
    "AT2": "Advanced Technique 2",
    "AT3": "Advanced Technique 3",
    "AT4": "Advanced Technique 4",
    "AL": "TM-Sidhi",
}


def _norm(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "").replace("_", "").replace(".", "").replace("/", "-")


def _course_stage(course: Course | None) -> str | None:
    if course is None:
        return None
    code = _norm(getattr(course, "code", "") or "")
    name = _norm(course.name or "")

    if code in {c.replace("-", "") for c in TM_CODES} or code in TM_CODES:
        return "TM"
    if "TM-ADULT" in name or "TM-COUPLE" in name or "TM-FAMILY" in name or "TM-STUDENT" in name or "WORDOFWISDOM" in name:
        return "TM"

    if code in {"AT1", "AT-1", "ATST", "AT-ST"} or "ADVANCEDTECHNIQUE1" in name or "ADVANCEDTECHNIQUEI" in name:
        return "AT1"
    if "ADVANCEDTECHNIQUE" in name and not any(token in name for token in {"2", "3", "4", "II", "III", "IV"}):
        return "AT1"
    if code in {"AT2", "AT-2"} or "ADVANCEDTECHNIQUE2" in name or "ADVANCEDTECHNIQUEII" in name:
        return "AT2"
    if code in {"AT3", "AT-3"} or "ADVANCEDTECHNIQUE3" in name or "ADVANCEDTECHNIQUEIII" in name:
        return "AT3"
    if code in {"AT4", "AT-4"} or "ADVANCEDTECHNIQUE4" in name or "ADVANCEDTECHNIQUEIV" in name:
        return "AT4"
    if code in {"AL", "SID", "SIDHI"} or "TM-SIDHI" in name or "SIDHI" in name:
        return "AL"
    return None


def get_person_enrolled_course_codes(person: Student | Prospect | Contact | None) -> set[str]:
    if isinstance(person, Contact):
        person = getattr(person, "prospect", None)
    if isinstance(person, Prospect):
        person = getattr(person, "student_record", None)
    if not isinstance(person, Student):
        return set()

    stages: set[str] = set()
    enrollments = Enrollment.objects.select_related("session__course").filter(student=person)
    for enrollment in enrollments:
        stage = _course_stage(enrollment.session.course if enrollment.session_id else None)
        if stage:
            stages.add(stage)
    return stages


def get_missing_prerequisites(person: Student | Prospect | Contact | None, selected_course: Course) -> list[str]:
    selected_stage = _course_stage(selected_course)
    if not selected_stage:
        return []
    needed = COURSE_PREREQUISITES.get(selected_stage, [])
    if not needed:
        return []
    existing = get_person_enrolled_course_codes(person)
    return [item for item in needed if item not in existing]


def is_eligible_for_course(person: Student | Prospect | Contact | None, selected_course: Course) -> bool:
    return len(get_missing_prerequisites(person, selected_course)) == 0


def _message_for_missing(missing: list[str], selected_course: Course) -> str:
    selected_stage = _course_stage(selected_course)
    selected_label = COURSE_LABELS.get(selected_stage or "", selected_course.name)
    if not missing:
        return "Eligible"
    if selected_stage == "AT1":
        return "This person must first enroll in Transcendental Meditation before enrolling in Advanced Technique 1."
    if selected_stage == "AT2":
        return "This person must first enroll in Advanced Technique 1 before enrolling in Advanced Technique 2."
    if selected_stage == "AL":
        return "This person must complete the progression through Advanced Technique 4 before enrolling in TM-Sidhi."
    if len(missing) == 1:
        return f"This person must first enroll in {COURSE_LABELS.get(missing[0], missing[0])} before enrolling in {selected_label}."
    missing_text = ", ".join(COURSE_LABELS.get(code, code) for code in missing)
    return f"This person is missing prerequisites for {selected_label}: {missing_text}."


def validate_course_eligibility(person: Student | Prospect | Contact | None, selected_course: Course) -> None:
    missing = get_missing_prerequisites(person, selected_course)
    if missing:
        raise ValidationError(_message_for_missing(missing, selected_course))


@dataclass
class EligibilityCheckResult:
    eligible: bool
    missing: list[str]
    message: str


def check_course_eligibility(person: Student | Prospect | Contact | None, selected_course: Course) -> EligibilityCheckResult:
    missing = get_missing_prerequisites(person, selected_course)
    return EligibilityCheckResult(
        eligible=not missing,
        missing=missing,
        message=_message_for_missing(missing, selected_course),
    )
