# mcms_app/signals.py
from django.db.models.signals import post_save, pre_save # Might need pre_save later, but start with post_save
from django.dispatch import receiver
from .models import *
from django.core.exceptions import ValidationError
from django.utils import timezone # If you need timezone for Deposit signal



# Signal for InventoryTransaction: Update Inventory when a transaction is created
@receiver(post_save, sender=InventoryTransaction)
def update_inventory_on_transaction(sender, instance, created, **kwargs):
    """Update inventory when a new InventoryTransaction is created"""
    if created:
        Inventory.update_inventory(instance.motorcycle_model)


# Signal for SupplierDeliveryItem: Create InventoryTransaction when a new item is saved
@receiver(post_save, sender=SupplierDeliveryItem)
def create_inventory_transaction_on_delivery_item_save(sender, instance, created, **kwargs):
    """
    Create an InventoryTransaction when a new SupplierDeliveryItem is created.
    This replaces the logic previously in SupplierDeliveryItem.save().
    """
    if created: # This ensures it only runs for NEW delivery items
        try:
            # IMPORTANT: Ensure 'instance.delivery' is already linked and saved
            # This signal runs AFTER instance.save() in the view/formset is done.
            # So instance.pk and instance.delivery are fully populated.
            InventoryTransaction.objects.create(
                transaction_type='SUPPLIER_DELIVERY',
                motorcycle_model=instance.motorcycle_model,
                quantity=instance.delivered_quantity,
                reference_model='SupplierDeliveryItem',
                reference_id=instance.pk,
                # Ensure instance.delivery.payment.supplier.name is accessible and doesn't cause errors
                remarks=f"Delivery from {instance.delivery.payment.supplier.name} - {instance.delivery.delivery_reference}"
            )
        except Exception as e:
            # Log this error. Signals should be robust.
            print(f"ERROR: Failed to create InventoryTransaction for SupplierDeliveryItem {instance.pk}: {e}")
            # Consider sending an alert (e.g., via email) in a production environment