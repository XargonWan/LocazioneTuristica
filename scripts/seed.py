from app.db import SessionLocal, init_db
from app.models import User, Settings
from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def create_admin(session):
    if session.query(User).count() == 0:
        admin = User(username="admin", role="admin", must_change_password=True)
        admin.password_hash = pwd_context.hash("admin")
        session.add(admin)
        print("Created default admin user with username 'admin' and password 'admin' (must_change_password=True)")
        session.commit()


def create_default_settings(session):
    if not session.query(Settings).filter_by(key="default_iva").first():
        s = Settings(key="default_iva", value="22.0")
        session.add(s)
    if not session.query(Settings).filter_by(key="max_upload_size").first():
        s2 = Settings(key="max_upload_size", value=str(10 * 1024 * 1024))
        session.add(s2)
    session.commit()


if __name__ == "__main__":
    init_db()
    session = SessionLocal()
    create_admin(session)
    create_default_settings(session)
    # seed a sample cleaning company and service if none exist
    from app.models import Company, CleaningService
    if session.query(Company).filter_by(is_cleaning_company=True).count() == 0:
        comp = Company(company_name="Ditta Pulisci", is_cleaning_company=True)
        session.add(comp)
        session.commit()
        svc = CleaningService(company_id=comp.id, name="Pulizia standard", default_amount=50.0, is_net=False, vat_percent=22.0)
        session.add(svc)
        session.commit()
        print("Seeded sample cleaning company and service")
    session.close()
    print("Database initialized and seeded.")
