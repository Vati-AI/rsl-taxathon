app_name = "vati_tax"
app_title = "Vati Tax"
app_publisher = "Vati AI"
app_description = "RSL Taxathon application and taxpayer portal"
app_email = "keletso@vatiai.com"
app_license = "Proprietary"
# Add role/permission hooks only with the tests in TAX-029.

# TAX-021: the Vue taxpayer portal owns client routes under /taxpayer; every
# deep link renders the guarded www/taxpayer page.
website_route_rules = [
    {"from_route": "/taxpayer/<path:app_path>", "to_route": "taxpayer"},
]
