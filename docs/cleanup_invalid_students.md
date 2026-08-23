# Invalid Student Cleanup

Use the `cleanup_invalid_students` management command to audit historical
Prospect-to-Student conversions against the financial eligibility rule.

## Dry run

Dry-run is the default and never modifies the database:

```bash
python manage.py cleanup_invalid_students --dry-run
```

Limit output to one or more Students by repeating `--student-id`:

```bash
python manage.py cleanup_invalid_students --dry-run \
  --student-id 1031 \
  --student-id 1032
```

The report includes Student, Prospect, Contact, and invoice identifiers; UUIDs;
invoice totals; confirmed payments at conversion; outstanding balances;
timestamp source; dependencies; violation reason; and proposed action. Every
dependent record is listed with its model/type, identifier, relevant state, and
planned disposition (`PRESERVE`, `REASSIGN`, or `MERGE`) before execution.

## Execution safeguards

Execution is intentionally unavailable as an unscoped bulk operation. It
requires reviewed Student IDs and an exact invalid-record confirmation count:

```bash
python manage.py cleanup_invalid_students --execute \
  --student-id 1032 \
  --confirm-count 1
```

All selected records execute inside one outer Django `transaction.atomic()`
boundary. Each record is re-audited and row-locked through the ORM before it is
changed. A known blocker fails preflight before mutation; an unexpected database
integrity error rolls back the entire selected set. Success messages are emitted
only after that transaction commits, so the command does not report rolled-back
records as changed.

Before commit, execution compares the exact primary-key sets for Contacts,
Prospects, donation statements/invoices, and payments. Any addition or removal
from those protected sets raises an integrity error and rolls back the entire
cleanup transaction.

## Verification

Run the read-only verification mode after cleanup:

```bash
python manage.py cleanup_invalid_students --verify
```

Verification uses the application's current Prospect conversion eligibility
service. It fails if an active, fully marked Prospect→Student lifecycle lacks a
required issued invoice, lacks confirmed payment, has an outstanding balance,
has inconsistent conversion markers, or violates the one-Prospect-per-Contact
identity relationship. It reports pre-conversion enrollment shells and inactive
historical shells separately because those rows are required by the current
enrollment/accounting schema and are not active Student lifecycle records.

`--verify` never modifies data and returns a command error when violations
remain, making it suitable for automated checks.

The lifecycle restoration never deletes the Contact or creates a replacement
Contact/Prospect. It reuses the Student's original protected one-to-one Prospect
and that Prospect's original protected one-to-one Contact. An archived Prospect
is unarchived, its conversion fields are cleared, and its status is set to
`qualified` by default.

The current schema does not allow a Student's original Prospect or the
Prospect's Contact to be deleted: both relationships are required and use
`PROTECT`. Therefore the command restores those records directly instead of
attempting fuzzy reconstruction from names, email addresses, or phone numbers.
This also prevents duplicate Contacts and Prospects.

Student-linked inquiries and communications are moved to that existing Prospect.
If no Student-only history remains, the empty Student row is removed. If an
enrollment, invoice/payment chain, interview, or meditator record must be
preserved, the Student row remains only as an `inactive` historical parent; it
is excluded from the normal active Student workflow and can be reused if the
Prospect later becomes financially eligible and is converted again.

Before deciding whether the Student shell can be removed, the command audits
direct and indirect dependencies: enrollments, course sessions, completed-course
attendance state, check-in communications, donation statements/invoices,
payments, disbursements, inquiries, communications, interviews, Meditator
records/events, and notes on each of those records. Issued invoices and recorded
payments are retained as accounting history. Course/session, attendance,
interview, and Meditator history are also retained on an inactive Student shell.
If no Student-only history requires a shell, Student inquiries and
communications are reassigned to the restored Prospect before the shell is
removed. If an inactive shell must remain, those records keep their original
Student, enrollment, and check-in links so the historical lifecycle stage is
not rewritten. Unique Student notes are copied into the Prospect notes for
continued operational visibility.

The dependency check fails closed for future Student relationships that do not
yet have an explicit cleanup policy: an unclassified dependent forces retention
of the inactive Student shell instead of allowing a cascade delete.

## Meditator integrity violations

An invalid Student with an active Meditator profile is reported as a **serious
integrity violation**. Execution does not delete the Meditator profile or its
transition events. Instead, it marks the profile inactive, records the
invalidation timestamp and reason, appends structured invalidation evidence to
the profile metadata, and retains the inactive Student shell required by that
audit chain. Invalidated profiles are excluded from active Meditator lists,
dashboard reporting, and bulk communications. A later legitimate Student
conversion can reactivate the same profile without discarding its prior
invalidation history.

The only dependency that blocks automatic restoration is another Prospect
referencing the same Student as its conversion result. That is an identity
ambiguity and requires manual review; the command will not guess which Prospect
owns the Student.

Records whose conversion time falls back to `Student.created_at` are also
skipped unless the operator explicitly supplies `--include-inferred` after
reviewing the evidence.

Use `--revert-status` to select another non-converted Prospect status.
