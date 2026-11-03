"""Authenticated app smoke endpoint; no tax data or decision behavior."""
import frappe

@frappe.whitelist()
def health():
    return {"application": "vati_tax", "status": "scaffold", "live_integrations": False}
