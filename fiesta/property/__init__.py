"""fiesta.property — S7 Property Owner

Captures rental property, landlord, and home-office allocation for the
home-office-rent deduction under Inland Revenue Act §6 (wholly+exclusively+
necessarily test).

Architecturally siblings of fiesta.deductions:
    - models.py       SQLAlchemy: Property, Landlord, RentalAgreement, LandlordRelationshipDetection
    - routes.py       Flask blueprint /property
    - related_party.py§195 detector integration (defers to fiesta.compliance.related_party)
    - sanity.py       Rs/sqft market band check + home-office % cap
"""
