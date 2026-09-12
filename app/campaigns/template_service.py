"""
Message Template Engine.
"""
import re
from app.models.contact import Contact
from app.models.campaign import Campaign
from app.campaigns.exceptions import TemplateValidationError

class MessageTemplateService:
    """Service to validate and render message templates."""
    
    ALLOWED_VARIABLES = {"name", "company", "city", "campaign"}
    
    FALLBACKS = {
        "name": "there",
        "company": "your company",
        "city": "your city"
    }
    
    # Matches {{variable_name}}
    VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\s*\}\}")

    @classmethod
    def validate_template(cls, template: str) -> None:
        """
        Validates that a template only contains allowed variables.
        Raises TemplateValidationError if an unsupported variable is found.
        """
        if not template:
            raise TemplateValidationError("Template cannot be empty.")
            
        found_vars = set(cls.VAR_PATTERN.findall(template))
        unsupported = found_vars - cls.ALLOWED_VARIABLES
        
        if unsupported:
            raise TemplateValidationError(
                f"Unsupported template variables: {', '.join(unsupported)}",
                errors=list(unsupported)
            )

    @classmethod
    def render_message(cls, template: str, contact: Contact, campaign: Campaign) -> str:
        """
        Renders a message template with contact and campaign data.
        """
        cls.validate_template(template)
        
        # Build dictionary of replacements
        replacements = {}
        
        # Name
        if contact.name and contact.name.strip():
            replacements["name"] = contact.name.strip()
        else:
            replacements["name"] = cls.FALLBACKS["name"]
            
        # Company
        if contact.company and contact.company.strip():
            replacements["company"] = contact.company.strip()
        else:
            replacements["company"] = cls.FALLBACKS["company"]
            
        # City
        if contact.city and contact.city.strip():
            replacements["city"] = contact.city.strip()
        else:
            replacements["city"] = cls.FALLBACKS["city"]
            
        # Campaign
        replacements["campaign"] = campaign.name
        
        # Perform replacements safely
        rendered = template
        for var_name, value in replacements.items():
            pattern = re.compile(r"\{\{\s*" + var_name + r"\s*\}\}")
            rendered = pattern.sub(value, rendered)
            
        return rendered
