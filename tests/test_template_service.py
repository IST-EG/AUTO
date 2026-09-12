import pytest
from app.campaigns.template_service import MessageTemplateService
from app.campaigns.exceptions import TemplateValidationError
from app.models.contact import Contact
from app.models.campaign import Campaign

def test_validate_template_valid():
    template = "Hi {{name}}, this is {{campaign}}."
    MessageTemplateService.validate_template(template) # should not raise

def test_validate_template_invalid():
    template = "Hi {{name}}, your balance is {{balance}}."
    with pytest.raises(TemplateValidationError) as exc:
        MessageTemplateService.validate_template(template)
    assert "balance" in str(exc.value)

def test_render_message_success():
    contact = Contact(name="Ahmed", company="Acme", city="Cairo")
    campaign = Campaign(name="Promo 2026")
    template = "Hello {{name}} from {{company}} in {{city}}, join {{campaign}}!"
    result = MessageTemplateService.render_message(template, contact, campaign)
    assert result == "Hello Ahmed from Acme in Cairo, join Promo 2026!"

def test_render_message_fallbacks():
    contact = Contact(name="", company=None, city="  ")
    campaign = Campaign(name="Promo 2026")
    template = "Hello {{name}} from {{company}} in {{city}}!"
    result = MessageTemplateService.render_message(template, contact, campaign)
    assert result == "Hello there from your company in your city!"
