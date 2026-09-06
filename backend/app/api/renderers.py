"""Turning domain objects into response models, for more than one route.

These three functions lived as private helpers in `task_routes` while it was
the only place a task, an event or a weekly report was rendered. The report
history endpoints now render the same weekly report — a stored snapshot and a
freshly built one have to be the same shape, or the UI would need two readers
for one report — so they move here rather than being written a second time.

Nothing about them changed in the move. `task_routes` imports them and its
behaviour is unaltered, which is the point: this is a relocation, not a
redesign.

Why the API layer and not the schema layer: each of these calls into a service
to compute the fields that are deliberately *not* columns — urgency, overdue,
whether an escalation is required, whether a past meeting has been answered
for. A schema module that did that would make request validation depend on the
task domain, and the boundary this project keeps is that schemas describe
shapes and the edge assembles them.
"""

from datetime import datetime

from app.models.calendar import CalendarEvent
from app.models.task import Task
from app.schemas.task_schema import (
    EscalationResponse,
    EventResponse,
    ReportSummary,
    TaskResponse,
    WeeklyReportResponse,
)
from app.services.features.calendar import event_service
from app.services.features.reports.weekly_report_service import WeeklyReport
from app.services.features.tasks import signal, task_service


def task_response(task: Task, *, now: datetime | None = None) -> TaskResponse:
    """One task plus the domain's verdict, in one shape.

    Built here rather than by `from_attributes` alone because six of the fields
    — `display_status`, `urgency`, `is_overdue` and the three `escalation_*`
    conclusions — are computed from the clock and the configured thresholds and
    are deliberately not columns.
    """

    assessment = task_service.assess(task, now=now)
    verdict = assessment.urgency
    escalation = assessment.escalation

    return TaskResponse(
        id=task.id,
        title=task.title,
        description=task.description,
        status=verdict.status,
        display_status=verdict.display_status,
        urgency=verdict.urgency,
        is_overdue=verdict.is_overdue,
        signal=signal.signal_for(status=task.status, due_at=task.due_at, now=now),
        days_overdue=signal.days_overdue(task.due_at, now),
        priority=task.priority,
        due_at=task.due_at,
        completed_at=task.completed_at,
        source=task.source,
        source_message_id=task.source_message_id,
        source_thread_id=task.source_thread_id,
        contact_name=task.contact_name,
        contact_address=task.contact_address,
        contact_display=task.contact_display,
        escalation_requested=task.escalation_requested,
        escalation_note=task.escalation_note,
        escalation_required=escalation.required,
        escalation_reason=escalation.reason,
        escalation_action=escalation.recommended_action,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


def event_response(
    event: CalendarEvent, *, now: datetime | None = None
) -> EventResponse:
    """One event, with what was recorded and how it reads kept apart."""

    verdict = event_service.assess(event, now=now)

    return EventResponse(
        id=event.id,
        title=event.title,
        description=event.description,
        location=event.location,
        starts_at=event.starts_at,
        ends_at=event.ends_at,
        status=verdict.status,
        display_status=verdict.display_status,
        needs_answer=verdict.needs_answer,
        is_past=verdict.is_past,
        attendance_evidence=event.attendance_evidence,
        attendance_recorded_at=event.attendance_recorded_at,
        attendance_note=event.attendance_note,
        organiser_name=event.organiser_name,
        organiser_address=event.organiser_address,
        source=event.source,
        created_at=event.created_at,
        updated_at=event.updated_at,
    )


def weekly_report_response(report: WeeklyReport) -> WeeklyReportResponse:
    """The whole weekly report, rendered.

    `report.generated_at` is passed down to every nested render so that a
    snapshot taken at one moment is internally consistent: a task cannot be
    "due in 2 days" in one section and "overdue" in another because the clock
    moved between two list comprehensions.
    """

    def section(items: list) -> list[TaskResponse]:
        return [task_response(item.task, now=report.generated_at) for item in items]

    def events(items: list) -> list[EventResponse]:
        return [event_response(item.event, now=report.generated_at) for item in items]

    return WeeklyReportResponse(
        generated_at=report.generated_at,
        period_start=report.period_start,
        period_end=report.period_end,
        completed_count=report.completed_count,
        due_soon_count=report.due_soon_count,
        overdue_count=report.overdue_count,
        escalation_count=report.escalation_count,
        summary=ReportSummary(
            completed_count=report.completed_count,
            due_soon_count=report.due_soon_count,
            overdue_count=report.overdue_count,
            escalation_count=report.escalation_count,
            events_needing_answer_count=len(report.events_needing_answer),
        ),
        upcoming=section(report.upcoming),
        overdue=section(report.overdue),
        completed=section(report.completed),
        blocked=section(report.blocked),
        follow_ups=section(report.follow_ups),
        high_priority=section(report.high_priority),
        deadlines=section(report.deadlines),
        escalations=[
            EscalationResponse(
                task=task_response(item.task, now=report.generated_at),
                reason=item.reason,
                age_seconds=item.age_seconds,
                recommended_action=item.recommended_action,
                contact_name=item.contact_name,
                contact_address=item.contact_address,
                escalation_contact_name=item.escalation_contact_name,
                escalation_contact_address=item.escalation_contact_address,
                escalation_contact_display=item.escalation_contact_display,
                target_source=item.escalation.target_source,
            )
            for item in report.escalations
        ],
        calendar_connected=report.calendar_connected,
        calendar_detail=report.calendar_detail,
        events=events(report.events),
        events_needing_answer=events(report.events_needing_answer),
    )
