from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Boolean, ForeignKey, DECIMAL, Text
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=True)
    role = Column(String, nullable=False, default="admin")  # admin|readonly
    must_change_password = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Settings(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    key = Column(String, unique=True, nullable=False)
    value = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class PropertyManager(Base):
    __tablename__ = "property_manager"
    id = Column(Integer, primary_key=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    company_name = Column(String, nullable=True)
    date_of_birth = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    tax_id = Column(String, nullable=True)
    iban = Column(String, nullable=True)
    percent = Column(DECIMAL(5, 2), default=0.0)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Company(Base):
    __tablename__ = "company"
    id = Column(Integer, primary_key=True)
    company_name = Column(String, nullable=False)
    contact_name = Column(String, nullable=True)
    address = Column(Text, nullable=True)
    tax_id = Column(String, nullable=True)
    iban = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    # indicate if this is a cleaning company (used to filter when selecting services)
    is_cleaning_company = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Platform(Base):
    __tablename__ = "platform"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    link = Column(String, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)


class Apartment(Base):
    __tablename__ = "apartment"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    address = Column(Text, nullable=True)
    locker_code = Column(String, nullable=True)
    property_manager_id = Column(Integer, ForeignKey("property_manager.id"), nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    property_manager = relationship("PropertyManager")


class Recurrence(Base):
    __tablename__ = "recurrence"
    id = Column(Integer, primary_key=True)
    type = Column(String, nullable=False, default="none")  # none|monthly|yearly
    interval = Column(Integer, default=1)
    start_date = Column(String, nullable=True)
    end_date = Column(String, nullable=True)
    next_date = Column(String, nullable=True)
    notes = Column(Text, nullable=True)


class Expense(Base):
    __tablename__ = "expense"
    id = Column(Integer, primary_key=True)
    apartment_id = Column(Integer, ForeignKey("apartment.id"), nullable=True)
    date = Column(String, nullable=True)
    gross_amount = Column(DECIMAL(10, 2), default=0.0)
    vat_percent = Column(DECIMAL(5, 2), default=22.0)
    net_amount = Column(DECIMAL(10, 2), default=0.0)
    pm_percent = Column(DECIMAL(5, 2), default=0.0)
    pm_amount = Column(DECIMAL(10, 2), default=0.0)
    net_after_pm = Column(DECIMAL(10, 2), default=0.0)
    category = Column(String, nullable=True)
    # flag to indicate this expense corresponds to a cleaning activity
    is_cleaning = Column(Boolean, default=False)
    associated_pm_id = Column(Integer, ForeignKey("property_manager.id"), nullable=True)
    associated_company_id = Column(Integer, ForeignKey("company.id"), nullable=True)
    recurrence_id = Column(Integer, ForeignKey("recurrence.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    apartment = relationship("Apartment")
    associated_pm = relationship("PropertyManager")
    associated_company = relationship("Company")
    recurrence = relationship("Recurrence")


class Income(Base):
    __tablename__ = "income"
    id = Column(Integer, primary_key=True)
    apartment_id = Column(Integer, ForeignKey("apartment.id"), nullable=True)
    platform_id = Column(Integer, ForeignKey("platform.id"), nullable=True)
    date = Column(String, nullable=True)
    gross_amount = Column(DECIMAL(10, 2), default=0.0)
    vat_percent = Column(DECIMAL(5, 2), default=22.0)
    net_amount = Column(DECIMAL(10, 2), default=0.0)
    pm_percent = Column(DECIMAL(5, 2), default=0.0)
    pm_amount = Column(DECIMAL(10, 2), default=0.0)
    net_after_pm = Column(DECIMAL(10, 2), default=0.0)
    recurrence_id = Column(Integer, ForeignKey("recurrence.id"), nullable=True)
    associated_pm_id = Column(Integer, ForeignKey("property_manager.id"), nullable=True)
    notes = Column(Text, nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    apartment = relationship("Apartment")
    platform = relationship("Platform")
    associated_pm = relationship("PropertyManager")
    recurrence = relationship("Recurrence")


class CleaningService(Base):
    __tablename__ = "cleaning_service"
    id = Column(Integer, primary_key=True)
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False)
    name = Column(String, nullable=False)
    default_amount = Column(DECIMAL(10,2), default=0.0)
    # is the default_amount net? if true IVA will be calculated on top when logging
    is_net = Column(Boolean, default=False)
    vat_percent = Column(DECIMAL(5, 2), default=22.0)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    company = relationship("Company")


class Cleaning(Base):
    __tablename__ = "cleaning"
    id = Column(Integer, primary_key=True)
    apartment_id = Column(Integer, ForeignKey("apartment.id"), nullable=False)
    income_id = Column(Integer, ForeignKey("income.id"), nullable=True)
    company_id = Column(Integer, ForeignKey("company.id"), nullable=False)
    service_id = Column(Integer, ForeignKey("cleaning_service.id"), nullable=True)
    date = Column(String, nullable=True)
    gross_amount = Column(DECIMAL(10, 2), default=0.0)
    vat_percent = Column(DECIMAL(5, 2), default=22.0)
    net_amount = Column(DECIMAL(10, 2), default=0.0)
    is_net = Column(Boolean, default=False)
    notes = Column(Text, nullable=True)
    expense_id = Column(Integer, ForeignKey("expense.id"), nullable=True)
    created_by = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

    apartment = relationship("Apartment")
    income = relationship("Income")
    company = relationship("Company")
    service = relationship("CleaningService")
    expense = relationship("Expense")


class Attachment(Base):
    __tablename__ = "attachment"
    id = Column(Integer, primary_key=True)
    filename = Column(String, nullable=False)
    disk_path = Column(String, nullable=False)
    mimetype = Column(String, nullable=True)
    size = Column(Integer, nullable=True)
    uploaded_by = Column(String, nullable=True)
    expense_id = Column(Integer, ForeignKey("expense.id"), nullable=True)
    income_id = Column(Integer, ForeignKey("income.id"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    expense = relationship("Expense")
    income = relationship("Income")


class Payment(Base):
    __tablename__ = "payment"
    id = Column(Integer, primary_key=True)
    target_type = Column(String, nullable=True)
    target_id = Column(Integer, nullable=True)
    amount = Column(DECIMAL(10, 2), default=0.0)
    date = Column(String, nullable=True)
    method = Column(String, nullable=True)
    note = Column(Text, nullable=True)
    status = Column(String, default="completed")
    created_at = Column(DateTime, default=datetime.utcnow)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    user = Column(String, nullable=True)
    action = Column(String, nullable=False)
    resource_type = Column(String, nullable=True)
    resource_id = Column(Integer, nullable=True)
    payload_before = Column(Text, nullable=True)
    payload_after = Column(Text, nullable=True)
    timestamp = Column(DateTime, default=datetime.utcnow)

