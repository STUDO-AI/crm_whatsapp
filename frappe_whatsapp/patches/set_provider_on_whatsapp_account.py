import frappe


def execute():
    """Pin every pre-existing account to Meta Cloud API.

    The field default covers new rows and Frappe's column backfill, but being
    explicit here keeps the intent auditable: nothing silently changes provider
    when this layer lands.
    """
    if not frappe.db.has_column("WhatsApp Account", "provider"):
        return

    frappe.db.sql(
        """
        UPDATE `tabWhatsApp Account`
        SET provider = 'Meta Cloud API'
        WHERE IFNULL(provider, '') = ''
        """
    )
