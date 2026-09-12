"""
HTML UI View routes for Web Control Center.
"""

from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.models.user import User
from app.web.config import web_settings
from app.web.dependencies import get_db, get_current_user_optional, get_current_user
from app.web.services.bootstrap_service import BootstrapService
from app.web.services.auth_service import AuthService
from app.web.services.dashboard_service import DashboardService
from app.web.services.campaign_service import CampaignService
from app.web.services.template_service import TemplateService
from app.models.campaign import Campaign
from app.web.services.contact_service import ContactService
from app.web.services.queue_service import WebQueueService
from app.web.services.campaign_contact_service import CampaignContactService

templates_dir = Path(__file__).resolve().parent.parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

router = APIRouter(include_in_schema=False)


@router.get("/", response_class=HTMLResponse)
def index(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Entry point router: redirects to setup, dashboard, or login."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if current_user:
        return RedirectResponse(url="/dashboard", status_code=302)
    return RedirectResponse(url="/login", status_code=302)


@router.get("/setup", response_class=HTMLResponse)
def setup_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders the first-run OWNER bootstrap page if available."""
    if not BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="setup.html",
        context={"current_user": current_user}
    )


@router.get("/login", response_class=HTMLResponse)
def login_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders the login page or redirects to setup/dashboard."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if current_user:
        return RedirectResponse(url="/dashboard", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={"current_user": None}
    )


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders authenticated dashboard placeholder or redirects to login."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    snapshot = DashboardService.get_dashboard_snapshot(db)
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={"current_user": current_user, "snapshot": snapshot}
    )


@router.get("/logout")
def logout_view(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Clears authentication session and redirects to login."""
    raw_token = request.cookies.get(web_settings.WEB_SESSION_COOKIE_NAME)
    if raw_token:
        AuthService.logout(db, raw_token, current_user)

    redirect = RedirectResponse(url="/login", status_code=302)
    redirect.delete_cookie(key=web_settings.WEB_SESSION_COOKIE_NAME, path="/")
    redirect.delete_cookie(key=web_settings.WEB_CSRF_COOKIE_NAME, path="/")
    return redirect


# ==============================================================================
# CAMPAIGNS UI
# ==============================================================================

@router.get("/campaigns", response_class=HTMLResponse)
def campaigns_page(
    request: Request,
    status: str = None,
    search: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders campaigns list view."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    campaigns = CampaignService.list_campaigns(db, status_filter=status, search=search)
    return templates.TemplateResponse(
        request=request,
        name="campaigns/list.html",
        context={
            "current_user": current_user,
            "campaigns": campaigns,
            "selected_status": status or "",
            "search_query": search or "",
        }
    )


@router.get("/campaigns/new", response_class=HTMLResponse)
def campaign_new_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders campaign creation form."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    templates_list = TemplateService.list_templates(db)
    return templates.TemplateResponse(
        request=request,
        name="campaigns/create.html",
        context={"current_user": current_user, "templates_list": templates_list}
    )


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
def campaign_detail_page(
    campaign_id: int,
    request: Request,
    contact_status: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders campaign details, statistics, and participant contacts."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    campaign = CampaignService.get_campaign(db, campaign_id)
    contacts = CampaignContactService.list_campaign_contacts(
        db, campaign_id=campaign_id, status_filter=contact_status
    )
    available_contacts = ContactService.list_contacts(
        db, contact_status="active", is_privileged=True, limit=500
    )
    return templates.TemplateResponse(
        request=request,
        name="campaigns/detail.html",
        context={
            "current_user": current_user,
            "campaign": campaign,
            "contacts": contacts,
            "available_contacts": available_contacts,
            "contact_status": contact_status or "",
        }
    )


# ==============================================================================
# TEMPLATES UI
# ==============================================================================

@router.get("/templates", response_class=HTMLResponse)
def templates_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders message template library view."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    templates_list = TemplateService.list_templates(db)
    return templates.TemplateResponse(
        request=request,
        name="templates/list.html",
        context={"current_user": current_user, "templates_list": templates_list}
    )


@router.get("/templates/new", response_class=HTMLResponse)
def template_new_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders new template authoring page."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="templates/create.html",
        context={"current_user": current_user}
    )


@router.get("/templates/{template_id}", response_class=HTMLResponse)
def template_detail_page(
    template_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders template details and full immutable version history."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    template_data = TemplateService.get_template(db, template_id)
    return templates.TemplateResponse(
        request=request,
        name="templates/detail.html",
        context={"current_user": current_user, "template": template_data}
    )


# ==============================================================================
# CONTACTS UI
# ==============================================================================

@router.get("/contacts", response_class=HTMLResponse)
def contacts_page(
    request: Request,
    search: str = None,
    consent_status: str = None,
    contact_status: str = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders contacts directory."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    is_privileged = current_user.role in ("ADMIN", "OWNER")
    contacts = ContactService.list_contacts(
        db,
        search=search,
        consent_status=consent_status,
        contact_status=contact_status,
        is_privileged=is_privileged,
        limit=100
    )
    return templates.TemplateResponse(
        request=request,
        name="contacts/list.html",
        context={
            "current_user": current_user,
            "contacts": contacts,
            "search_query": search or "",
            "consent_status": consent_status or "",
            "contact_status": contact_status or "",
        }
    )


@router.get("/contacts/import", response_class=HTMLResponse)
def contacts_import_page(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders CSV import interface with dry-run verification."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    return templates.TemplateResponse(
        request=request,
        name="contacts/import.html",
        context={"current_user": current_user}
    )


@router.get("/contacts/{contact_id}", response_class=HTMLResponse)
def contact_detail_page(
    contact_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders single contact profile."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    contact = ContactService.get_contact(db, contact_id)
    return templates.TemplateResponse(
        request=request,
        name="contacts/detail.html",
        context={"current_user": current_user, "contact": contact}
    )


# ==============================================================================
# QUEUE & MESSAGE OPERATIONS UI
# ==============================================================================

@router.get("/queue", response_class=HTMLResponse)
def queue_list_page(
    request: Request,
    page: int = 1,
    campaign_id: Optional[int] = None,
    status: Optional[str] = None,
    contact_query: Optional[str] = None,
    retry_filter: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders Queue & Message Operations dashboard."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    queue_data = WebQueueService.list_messages(
        db=db,
        user=current_user,
        page=page,
        page_size=20,
        campaign_id=campaign_id,
        status=status,
        contact_query=contact_query,
        retry_filter=retry_filter,
    )
    stats = WebQueueService.get_queue_stats(db)
    campaigns = db.query(Campaign).order_by(Campaign.name).all()

    return templates.TemplateResponse(
        request=request,
        name="queue/list.html",
        context={
            "current_user": current_user,
            "queue_data": queue_data,
            "stats": stats,
            "campaigns": campaigns,
            "selected_campaign": campaign_id,
            "selected_status": status or "",
            "contact_query": contact_query or "",
            "selected_retry": retry_filter or "",
            "page": page,
        }
    )


@router.get("/queue/{message_id}", response_class=HTMLResponse)
def queue_detail_page(
    message_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_optional)
):
    """Renders single message inspection and operations view."""
    if BootstrapService.is_setup_available(db):
        return RedirectResponse(url="/setup", status_code=302)
    if not current_user:
        return RedirectResponse(url="/login", status_code=302)

    message_detail = WebQueueService.get_message_detail(db=db, user=current_user, message_id=message_id)
    return templates.TemplateResponse(
        request=request,
        name="queue/detail.html",
        context={
            "current_user": current_user,
            "message": message_detail,
        }
    )


