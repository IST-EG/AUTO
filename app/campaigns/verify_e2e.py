"""
Manual e2e verification script for Phase 2.
Run with: python -m app.campaigns.verify_e2e
"""
import sys
from datetime import datetime, timezone

from app.database import SessionLocal
from app.models import Contact
from app.contacts.manager import ContactManager
from app.campaigns.manager import CampaignManager
from app.campaigns.contact_manager import CampaignContactManager
from app.campaigns.template_service import MessageTemplateService
from app.campaigns.statistics_service import CampaignStatisticsService
from app.campaigns.eligibility_service import CampaignEligibilityService

def main():
    db = SessionLocal()
    
    try:
        print("1. Creating ContactManager and CampaignManager...")
        contact_mgr = ContactManager(db)
        camp_mgr = CampaignManager(db)
        cc_mgr = CampaignContactManager(db)
        stat_svc = CampaignStatisticsService(db)
        eligibility_svc = CampaignEligibilityService(db)
        
        print("2. Creating Campaign in DRAFT...")
        import time
        ts = int(time.time())
        camp = camp_mgr.create_campaign(f"E2E Test Campaign {ts}", "Hi {{name}} from {{company}}, this is {{campaign}}.")
        print(f" -> Campaign created: {camp.name} (Status: {camp.status})")
        
        print("3. Adding contacts...")
        # Create some contacts first with unique phone numbers
        p1 = f"+1415555{ts % 10000:04d}"
        p2 = f"+1415555{(ts + 1) % 10000:04d}"
        c1 = contact_mgr.create_contact("Alice", p1, "US", company="CorpA")
        c2 = contact_mgr.create_contact("Bob", p2, "US", company="CorpB", consent_status="opted_out")
        
        # 4. Run eligibility checks directly just to verify output
        elig_res1 = eligibility_svc.check_eligibility(c1, camp)
        print(f" -> Eligibility for {c1.name}: {elig_res1.eligible} ({elig_res1.reason})")
        
        elig_res2 = eligibility_svc.check_eligibility(c2, camp)
        print(f" -> Eligibility for {c2.name}: {elig_res2.eligible} ({elig_res2.reason})")
        
        # 5. Add contacts to campaign
        res = cc_mgr.add_multiple_contacts(camp, [c1, c2])
        print(f" -> Added multiple contacts result: {res}")
        
        # 6. Render personalized messages
        msg_alice = MessageTemplateService.render_message(camp.message_template, c1, camp)
        print(f" -> Rendered for Alice: '{msg_alice}'")
        
        # 7. Schedule campaign
        camp = camp_mgr.schedule_campaign(camp.id, datetime.now(timezone.utc))
        print(f" -> Campaign scheduled. Status: {camp.status}")
        
        # 8. Transition to RUNNING
        camp = camp_mgr.transition_state(camp.id, "RUNNING")
        print(f" -> Campaign transitioned. Status: {camp.status}")
        
        # 9. Update CampaignContact statuses manually (simulating a worker)
        from app.models.campaign_contact import CampaignContact
        cc = db.query(CampaignContact).filter_by(campaign_id=camp.id, contact_id=c1.id).first()
        cc.status = "SENT"
        db.commit()
        print(" -> Simulated sending to Alice.")
        
        # 10. Calculate statistics
        stats = stat_svc.get_statistics(camp.id)
        print(f" -> Statistics: {stats}")
        
        # 11. Transition to COMPLETED
        camp = camp_mgr.transition_state(camp.id, "COMPLETED")
        print(f" -> Campaign finished. Status: {camp.status}")
        
        # 12. Verify audit logs
        from app.models.audit_log import AuditLog
        logs = db.query(AuditLog).filter_by(campaign_id=camp.id).all()
        print(f" -> Audit logs count: {len(logs)}")
        for log in logs:
            print(f"    - [{log.created_at}] {log.event_type} - {log.status} (contact: {log.contact_id})")

    except Exception as e:
        print(f"Error during E2E verification: {e}")
        db.rollback()
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
