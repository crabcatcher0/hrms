import datetime

from django.contrib.auth import authenticate
from django.contrib.auth import login
from django.contrib.auth import logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models import Sum
from django.db.models.functions import Coalesce
from django.http import HttpRequest
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.csrf import ensure_csrf_cookie
from ninja import NinjaAPI
from ninja.pagination import paginate  # type: ignore
from ninja.security import django_auth

from core.models import AbsenceBalance
from core.models import Activity
from core.models import Holiday
from core.models import Project
from core.models import TimeLog
from core.models import User
from core.schemas import AbsenceBalanceDTO
from core.schemas import ActivityDTO
from core.schemas import ChangePassword
from core.schemas import CreateActivity
from core.schemas import CreateProject
from core.schemas import CreateUser
from core.schemas import GenericDTO
from core.schemas import HolidayDTO
from core.schemas import Login
from core.schemas import ProjectDTO
from core.schemas import RemainingAbsences
from core.schemas import StartTimeLog
from core.schemas import SubmitAbsence
from core.schemas import TimeLogDTO
from core.schemas import TimeLogSummaryDTO
from core.schemas import TimeLogSummaryPerDay
from core.schemas import UserDTO

api = NinjaAPI(docs_url="/docs/", csrf=True)


@api.exception_handler(ValidationError)
def django_validation_error(request: HttpRequest, exc: ValidationError):
    return api.create_response(
        request,
        {
            "detail": [
                {
                    "type": "validation_error",
                    "loc": [],  # impossible to find out
                    "msg": e,
                }
                for e in exc.messages
            ]
        },
        status=422,
    )


@api.post("/csrf/")
@ensure_csrf_cookie
@csrf_exempt
def get_csrf_token(request: HttpRequest):
    return HttpResponse()


@api.get("/projects/", response=list[ProjectDTO], auth=django_auth)
@paginate
def list_projects(request: HttpRequest):
    return Project.objects.all().order_by("-id")


@api.post("/projects/", response=GenericDTO, auth=django_auth)
def create_project(request: HttpRequest, project: CreateProject):
    try:
        Project.objects.create(name=project.project)
        return 200, {"detail": "Project created successfully."}
    except Exception:
        return 400, {"detail": "Something went wrong."}


@api.get("/activities/", response=list[ActivityDTO], auth=django_auth)
@paginate
def list_activities(request: HttpRequest):
    return Activity.objects.all().order_by("-id")


@api.post("/activities/", response=GenericDTO, auth=django_auth)
def create_activity(request: HttpRequest, activity: CreateActivity):
    try:
        Activity.objects.create(name=activity.activity)
        return 200, {"detail": "Activity created successfully."}
    except Exception:
        return 400, {"detail": "Something went wrong."}


@api.get("/users/current/", response=UserDTO, auth=django_auth)
def current_user(request: HttpRequest):
    return request.user


@api.post(
    "/users/", response={200: UserDTO, 400: GenericDTO}, auth=django_auth
)
def create_user(request: HttpRequest, user: CreateUser):
    try:
        user_obj = User.objects.create_user(
            username=user.username,
            password=user.password,
            is_superuser=False,
        )
        user_obj.save()
        return user_obj
    except IntegrityError:
        return 400, {"detail": "Username already exists."}


@api.get(
    "/users/", response={200: list[UserDTO], 400: GenericDTO}, auth=django_auth
)
@paginate
def list_users(request: HttpRequest):
    if request.user.is_superuser:  # type: ignore
        user_obj = User.objects.all()
        return user_obj
    else:
        return 400, {"detail": "Unauthorized request."}


@api.post(
    "/users/change-password/",
    auth=django_auth,
    response={200: GenericDTO, 400: GenericDTO},
)
def change_password(request: HttpRequest, data: ChangePassword):
    user = request.user
    if not user.check_password(data.current_password):
        return 400, {"detail": "Current password is incorrect."}
    validate_password(data.new_password)
    user.set_password(data.new_password)
    user.save()
    update_session_auth_hash(request, user)  # type: ignore
    return GenericDTO(detail="Password changed successfully.")


@api.get(
    "/time-logs/",
    auth=django_auth,
    response={200: list[TimeLogDTO]},
)
@paginate
def list_time_logs(request: HttpRequest):
    if request.user.is_superuser:  # type: ignore
        objs = TimeLog.objects.all()
    else:
        objs = TimeLog.objects.filter(user=request.user)
    return objs.order_by("-id")


@api.get(
    "/time-logs/current/",
    auth=django_auth,
    response={200: TimeLogDTO, 404: GenericDTO},
)
def current_time_log(request: HttpRequest):
    obj = TimeLog.objects.filter(user=request.user, end=None).first()
    if not obj:
        return 404, {"detail": "Not found."}
    return obj


@api.post(
    "/time-logs/start/",
    auth=django_auth,
    response={200: TimeLogDTO, 400: GenericDTO},
)
def start_time_log(request: HttpRequest, data: StartTimeLog):
    try:
        if TimeLog.objects.filter(user=request.user, end=None).exists():
            return 400, {"detail": "An active session already exists."}
        obj = TimeLog.objects.create(
            user=request.user,
            start=timezone.now(),
            end=None,
            project=Project.objects.get(id=data.project),
            activity=Activity.objects.get(id=data.activity),
        )
        obj.save()
        return obj
    except Project.DoesNotExist:
        return 400, {"detail": "Project does not exist."}
    except Activity.DoesNotExist:
        return 400, {"detail": "Activity does not exist."}


@api.post("/time-logs/end/", auth=django_auth, response=GenericDTO)
def end_time_log(request: HttpRequest):
    TimeLog.objects.filter(user=request.user, end=None).update(
        end=timezone.now()
    )
    return {"detail": "Success."}


@api.get(
    "/time-logs/summary/",
    auth=django_auth,
    response={200: list[TimeLogSummaryDTO]},
)
def time_log_summary(
    request: HttpRequest, start: datetime.date, end: datetime.date
):
    # database
    logs = TimeLog.objects.filter(
        start__date__gte=start,
        start__date__lte=end,
        end__isnull=False,
    )
    if request.user.is_superuser:  # type: ignore
        users = User.objects.all()
    else:
        users = User.objects.filter(id=request.user.pk)
        logs = logs.filter(user=request.user)
    logs = logs.values("user", "start", "end")
    holidays = Holiday.objects.filter(date__gte=start, date__lte=end)

    # data generation
    holidays_map = {h.date: h.name for h in holidays}
    output: list[TimeLogSummaryDTO] = []
    for u in users:
        user_data = TimeLogSummaryDTO(user=u.username, summary=[])
        date = start
        while date <= end:
            logs_per_day = [
                l
                for l in logs
                if l["start"].date() == date and l["user"] == u.pk
            ]
            hours_worked = (
                sum(
                    [
                        (i["end"] - i["start"]).total_seconds()
                        for i in logs_per_day
                    ]
                )
                / 3600
            )
            weekday = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"][
                date.weekday()
            ]
            if date in holidays_map:
                holiday = holidays_map[date]
                expected_hours = 0
            else:
                holiday = ""
                expected_hours = getattr(u, f"expected_hours_{weekday}")
            user_data.summary.append(
                TimeLogSummaryPerDay(
                    date=date,
                    hours_worked=hours_worked,
                    weekday=weekday,
                    expected_hours=expected_hours,
                    holiday=holiday,
                )
            )
            date += datetime.timedelta(days=1)
        output.append(user_data)
    return output


@api.get(
    "/absence-balances/",
    auth=django_auth,
    response={200: list[AbsenceBalanceDTO]},
)
@paginate
def list_absence_balances(request: HttpRequest):
    if request.user.is_superuser:  # type: ignore
        objs = AbsenceBalance.objects.all()
    else:
        objs = AbsenceBalance.objects.filter(user=request.user)
    return objs.order_by("-id")


@api.get(
    "/absence-balances/remaining/",
    auth=django_auth,
    response={200: RemainingAbsences},
)
def remaining_absences(request: HttpRequest):
    obj = AbsenceBalance.objects.filter(user=request.user).aggregate(
        value=Coalesce(Sum("delta"), 0)
    )
    return obj


@api.post(
    "/absence-balances/submit/",
    auth=django_auth,
    response={200: GenericDTO, 400: GenericDTO},
)
def submit_absence(request: HttpRequest, data: SubmitAbsence):
    obj = AbsenceBalance.objects.filter(user=request.user).aggregate(
        value=Coalesce(Sum("delta"), 0)
    )
    if obj["value"] < 1:
        return 400, {"detail": "You have no absence balance."}
    AbsenceBalance.objects.create(
        user=request.user,
        date=data.date,
        description=data.description,
        delta=-1,
        created_by=request.user,
    )
    return 400, {"detail": "Success."}


@api.post("/auth/login/", response={200: GenericDTO, 400: GenericDTO})
def auth_login(request: HttpRequest, data: Login):
    user = authenticate(
        request,
        username=data.username,
        password=data.password,
    )
    if user is not None:
        login(request, user)
        return 200, {"detail": "Success."}
    else:
        return 400, {"detail": "Invalid credentials."}


@api.post("/auth/logout/", response=GenericDTO)
def auth_logout(request: HttpRequest):
    logout(request)
    return {"detail": "Success."}


@api.get("/holidays/", response=list[HolidayDTO], auth=django_auth)
@paginate
def list_holidays(request: HttpRequest):
    return Holiday.objects.all().order_by("-id")
