from django.db import models
from django.db.models import Sum
from django.utils import timezone
from django.dispatch import receiver
from django.db import models, transaction
from django.utils import timezone
from django.core.exceptions import ValidationError
from django.db.models import Sum, F, DecimalField, Q, Value
from decimal import Decimal
import uuid
from django.db.models.functions import Coalesce
from django.core.validators import MinValueValidator
from django.urls import reverse
from django.utils.functional import cached_property
import datetime



class Motorcycle(models.Model):
    ACTIVE = 'ACTIVE'
    DISCONTINUED = 'DISCONTINUED'
    # If you wanted a specific status for errors that were used:
    # ARCHIVED_AS_MISTAKE = 'ARCHIVED_MISTAKE'
    STATUS_CHOICES = [
        (ACTIVE, 'Active'),
        (DISCONTINUED, 'Discontinued'),
        # (ARCHIVED_AS_MISTAKE, 'Archived (Data Entry Error)'),
    ]

    name = models.CharField(max_length=100)
    brand = models.CharField(max_length=50)
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=ACTIVE,
        db_index=True, # Good for filtering by status
        help_text="Current status of the motorcycle model."
    )
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta:
        unique_together = ['name', 'brand']
        ordering = ['brand', 'name']

    def __str__(self):
        return f"{self.brand} {self.name}"

    def get_absolute_url(self):
        return reverse('motorcycle_detail', kwargs={'pk': self.pk})

    @cached_property
    def has_critical_dependencies(self):
        """
        Checks for dependencies that would prevent a hard delete.
        This includes any transactional history or current stock.
        """
        from .models import Sale, InventoryTransaction, SupplierPaymentItem, SupplierDeliveryItem, Inventory # Local import

        if Sale.objects.filter(motorcycle=self).exists():
            return True, "Sales records exist for this model."
        if InventoryTransaction.objects.filter(motorcycle_model=self).exists():
            return True, "Inventory transaction history exists for this model."
        if SupplierPaymentItem.objects.filter(motorcycle_model=self).exists():
            return True, "Supplier payment items exist for this model."
        # SupplierDeliveryItem check might be redundant if PaymentItem implies it, but good to be explicit
        if SupplierDeliveryItem.objects.filter(motorcycle_model=self).exists():
             return True, "Supplier delivery items exist for this model."
        
        # Check current inventory stock
        inventory_record = Inventory.objects.filter(motorcycle_model=self).first()
        if inventory_record and inventory_record.current_quantity != 0: # Or just inventory_record.exists() if any inventory record is an issue
            return True, f"There are still {inventory_record.current_quantity} units in stock."
        
        return False, ""

    @cached_property
    def can_be_discontinued(self):
        """
        Checks if this motorcycle model can be safely discontinued.
        Prevents discontinuation if it's part of active, undelivered supplier payment items.
        """

        active_undelivered_supplier_orders = SupplierPaymentItem.objects.filter(
            motorcycle_model=self,  # Filter for the current motorcycle instance
            payment__status=SupplierPayment.ACTIVE
        ).annotate(
            total_delivered_for_this_item=Coalesce(
                Sum(
                    # Path: From SupplierPaymentItem -> its payment -> that payment's deliveries -> those deliveries' items -> their quantity
                    'payment__deliveries__delivery_items__delivered_quantity',
                    filter=Q(payment__deliveries__is_cancelled=False) & # Ensure the delivery itself isn't cancelled
                           Q(payment__deliveries__delivery_items__motorcycle_model=self.pk) # Ensure we only sum items for THIS motorcycle model
                ),
                Value(0), # Default to 0 if no deliveries/items are found
                output_field=DecimalField()
            )
        ).filter(
            expected_quantity__gt=F('total_delivered_for_this_item') # Check if expected > delivered
        )

        if active_undelivered_supplier_orders.exists():
            return False, "Model is part of one or more active, undelivered supplier payment items."
        
        return True, ""

    
class Customer(models.Model):
    firstname = models.CharField(max_length=50, blank=False)
    lastname = models.CharField(max_length=50, blank=False)
    phone = models.CharField(max_length=50, blank=False)
    address = models.TextField(blank=False)
    
    @property
    def name(self):
        return f"{self.firstname} {self.lastname}"
    
    def __str__(self):
    # This will now display the customer's name in the dropdown
        return f"{self.firstname} {self.lastname}"
    

class Supplier(models.Model):
    """Supplier information"""
    name = models.CharField(max_length=200)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['name']
    
    def __str__(self):
        return self.name


class SupplierPayment(models.Model):
    """Records payments made to suppliers"""
    ACTIVE = 'ACTIVE'
    COMPLETED = 'COMPLETED'
    CANCELLED = 'CANCELLED'
    STATUS_CHOICES = [
        (ACTIVE, 'Active'),
        (COMPLETED, 'Completed'),
        (CANCELLED, 'Cancelled'),
    ]

    supplier = models.ForeignKey(
        Supplier,
        on_delete=models.CASCADE,
        related_name='payments'
    )
    payment_reference = models.CharField(
        max_length=50,
        unique=True,
        blank=True,
        null=True,
        help_text="Unique payment reference number"
    )
    amount_paid = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Total amount paid to supplier",
        validators=[MinValueValidator(Decimal('0.01'))] # Basic model-level check
    )
    payment_date = models.DateTimeField(default=timezone.now)
    payment_method = models.CharField(
        max_length=20,
        choices=[
            ('BANK_TRANSFER', 'Bank Transfer'),
            ('CASH', 'Cash'),
        ],
        default='BANK_TRANSFER'
    )
    remarks = models.TextField(blank=True)
    status = models.CharField(
            max_length=20,
            choices=STATUS_CHOICES,
            default=ACTIVE,
            db_index=True # Add db_index for better query performance on status
        )    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-payment_date', '-created_at']

    def __str__(self):
        formatted_amount = f"{self.amount_paid:,.2f}" 
        formatted_date = self.payment_date.strftime('%Y-%m-%d')
        ref_display = self.payment_reference or f"ID: {self.pk}" if self.pk else "New Payment"
        supplier_display = self.supplier.name if self.supplier else "N/A"

        return f"{ref_display} | {supplier_display} | ₦{formatted_amount} on {formatted_date}"

    def clean(self):
        super().clean() # Call parent's clean method
        
        # Prevent status changes from CANCELLED or COMPLETED back to ACTIVE directly in clean
        if self.pk: # if instance is being updated
            original_instance = SupplierPayment.objects.get(pk=self.pk)
            if original_instance.status == self.CANCELLED and self.status != self.CANCELLED:
                raise ValidationError("Cannot change status from Cancelled.")
            if original_instance.status == self.COMPLETED and self.status == self.ACTIVE:
                raise ValidationError("Cannot change status from Completed back to Active directly. This requires re-opening logic if necessary.")
        
        if self.amount_paid is not None and self.amount_paid <= Decimal('0.00'):
            raise ValidationError({'amount_paid': "Payment amount must be greater than zero."})

        if self.payment_date:
            payment_date = self.payment_date
            if isinstance(payment_date, datetime.datetime):
                payment_date = payment_date.date()

            if payment_date > timezone.now().date() + datetime.timedelta(days=365):
                raise ValidationError({'payment_date': "Payment date cannot be more than one year in the future."})

    @cached_property
    def total_expected_cost(self):
        # Return 0 if instance hasn't been saved yet
        if not self.pk:
            return Decimal('0.00')
        if not hasattr(self, 'payment_items'):
            return Decimal('0.00')
        return self.payment_items.aggregate(
            total=Sum(F('expected_quantity') * F('unit_price'))
        )['total'] or Decimal('0.00')

    @property
    def cost_difference(self):
        return self.amount_paid - self.total_expected_cost

    @cached_property
    def has_deliveries(self):
        # Return False if instance hasn't been saved yet
        if not self.pk:
            return False
        if not hasattr(self, 'deliveries'):
            return False
        return self.deliveries.filter(is_cancelled=False).exists() # SupplierDelivery still uses is_cancelled

    @property
    def is_editable(self):
        """Determines if the payment can be edited."""
        return self.status == self.ACTIVE and not self.has_deliveries

    @property
    def is_cancellable(self):
        """Determines if the payment can be cancelled."""
        return self.status == self.ACTIVE and not self.has_deliveries

    @cached_property
    def _calculate_total_expected_quantity(self):
        # Return 0 if instance hasn't been saved yet
        if not self.pk:
            return 0
        return self.payment_items.aggregate(
            total_expected=Coalesce(Sum('expected_quantity'), 0, output_field=models.IntegerField())
        )['total_expected']

    @cached_property
    def _calculate_total_delivered_quantity(self):
        # Return 0 if instance hasn't been saved yet
        if not self.pk:
            return 0
        # Clear approach: get all delivery items for this payment's deliveries
        total = 0
        for delivery in self.deliveries.filter(is_cancelled=False):
            delivery_total = delivery.delivery_items.aggregate(
                total=Sum('delivered_quantity')
            )['total'] or 0
            total += delivery_total
        return total

    def update_completion_status(self, force_recalculate=False):
        """
        Updates the payment status to COMPLETED if all items are fully delivered.
        Only transitions from ACTIVE to COMPLETED.
        """
        # Safety check: ensure the instance has been saved
        if not self.pk:
            return False
            
        if self.status == self.ACTIVE or force_recalculate:
            if not self.payment_items.exists():
                return False

            # Use the existing cached properties instead of recalculating
            total_expected = self._calculate_total_expected_quantity
            total_delivered = self._calculate_total_delivered_quantity

            print(f"Payment {self.payment_reference}: Expected={total_expected}, Delivered={total_delivered}")  # Debug

            if total_expected > 0 and total_delivered >= total_expected:
                if self.status != self.COMPLETED:
                    self.status = self.COMPLETED
                    self.save(update_fields=['status', 'updated_at'])
                    return True
            elif self.status == self.COMPLETED and total_delivered < total_expected:
                if force_recalculate:
                    self.status = self.ACTIVE
                    self.save(update_fields=['status', 'updated_at'])
                    return True

        return False

    @cached_property
    def is_fully_delivered(self):
        """Checks if the payment is marked as completed."""
        if self.status == self.COMPLETED:
            return True
        # For active payments, perform the calculation:
        if self.status == self.ACTIVE:
            # Return False if instance hasn't been saved yet
            if not self.pk:
                return False
            if not self.payment_items.exists():
                 return True # Or False, depending on business rule for payments with no items
            total_expected = self._calculate_total_expected_quantity
            total_delivered = self._calculate_total_delivered_quantity
            return total_expected > 0 and total_delivered >= total_expected
        return False

    def refresh_cached_properties(self):
        # Clear all cached properties to force recalculation
        cached_props = ['_calculate_total_expected_quantity', '_calculate_total_delivered_quantity', 'has_deliveries', 'total_expected_cost', 'is_fully_delivered']
        for prop in cached_props:
            if hasattr(self, prop):
                delattr(self, prop)

    def save(self, *args, **kwargs):
        # Clear cached properties that might be stale
        if hasattr(self, '_calculate_total_expected_quantity'):
            delattr(self, '_calculate_total_expected_quantity')
        if hasattr(self, '_calculate_total_delivered_quantity'):
            delattr(self, '_calculate_total_delivered_quantity')
        
        # Generate payment reference if not set
        if not self.payment_reference:
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.payment_reference = f"PAY-{uuid.uuid4().hex[:8].upper()}-{timestamp}"
        
        # Check if this is a new instance (no pk yet)
        is_new = self.pk is None
        
        super().save(*args, **kwargs)
        
        # Only check completion status for existing payments (not new ones)
        # New payments won't have payment_items yet, so no need to check completion
        if not is_new and self.status == self.ACTIVE and 'status' not in (kwargs.get('update_fields') or []):
            self.update_completion_status()


class SupplierPaymentItem(models.Model):
    """Items intended to be stocked with a supplier payment"""
    payment = models.ForeignKey(
        SupplierPayment,
        on_delete=models.CASCADE,
        related_name='payment_items'
    )
    motorcycle_model = models.ForeignKey(
        Motorcycle,
        on_delete=models.CASCADE,
        related_name='payment_items'
    )
    expected_quantity = models.PositiveIntegerField(
        default=1, # Consider a default if appropriate
        validators=[MinValueValidator(1)] # Ensures quantity is at least 1    unit_price = models.DecimalField(max_digits=10, decimal_places=2)
    )
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        validators=[MinValueValidator(Decimal('0.01'))] # Ensures price is at least 0.01
    )
    remarks = models.TextField(blank=True)
    
    class Meta:
        unique_together = ['payment', 'motorcycle_model']
    
    def __str__(self):
        return f"{self.motorcycle_model} - {self.expected_quantity} units @ ${self.unit_price}"
    
    @property
    def total_expected_cost(self):
        """Calculate total expected cost for this item"""
        return self.expected_quantity * self.unit_price
    
    def clean(self):
        super().clean()

        
        if self.payment_id and self.payment.status != SupplierPayment.ACTIVE: # Check new status
            # Also consider if payment.has_deliveries should still prevent editing items even if active
            if self.payment.has_deliveries: # Keep this check for active payments
                 raise ValidationError(f"Cannot edit payment items after deliveries have begun, even if payment is still Active.")

        # These are now largely handled by PositiveIntegerField/MinValueValidator
        # but explicit checks can provide more context if needed or if validators are removed.
        if self.expected_quantity is not None and self.expected_quantity <= 0:
            raise ValidationError({'expected_quantity': "Expected quantity must be greater than zero."})
        if self.unit_price is not None and self.unit_price <= Decimal('0.00'):
            raise ValidationError({'unit_price': "Unit price must be greater than zero."})

        # This requires the payment instance to be associated and its amount_paid to be known.
        if self.payment_id and self.expected_quantity is not None and self.unit_price is not None:
            try:
                pass
            except SupplierPayment.DoesNotExist:
                pass
    
    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)
        # After saving a payment item, the payment's completion status might change
        # (e.g. if expected_quantity is modified).
        if self.payment:
            self.payment.update_completion_status(force_recalculate=True)
    
    
    def get_absolute_url(self):
        return reverse('payment_detail', kwargs={'pk': self.pk})


class SupplierDelivery(models.Model):
    """Confirmation of what was actually delivered"""
    payment = models.ForeignKey(
        SupplierPayment,
        on_delete=models.CASCADE,
        related_name='deliveries'
    )
    delivery_reference = models.CharField(
        max_length=50,
        unique=True, # Add unique constraint for database integrity
        blank=True,  # Allow it to be blank in forms
        null=True,   # Allow it to be NULL in the database
        help_text="Auto-generated delivery reference number"
    )
    delivery_date = models.DateField(default=timezone.now)
    remarks = models.TextField(blank=True)
    is_cancelled = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-delivery_date', '-created_at']

    def __str__(self):
        # Handle cases where delivery_reference might not be set yet (e.g., new unsaved object)
        ref = self.delivery_reference or f"New ({self.pk})" if self.pk else "New"
        payment_ref = self.payment.payment_reference if self.payment else "N/A"
        return f"Delivery {ref} for Payment {payment_ref}"

    def clean(self):
        """Validate delivery data"""
        if self.payment.status == "CANCELLED": # Check self.payment first
            print(self.payment.status)
            raise ValidationError("Cannot create delivery for cancelled payment")

        # Defensive check for payment_items on self.payment
        if self.payment and not self.payment.payment_items.exists():
            raise ValidationError("Cannot create delivery for payment with no items")

        # Add a check for uniqueness if it's not handled by the database
        # (Though `unique=True` field handles this, this is good for early validation)
        if not self.delivery_reference: # If it's being auto-generated
            # No need to check uniqueness here if `save` method does it right after generation.
            pass # Remove the old unique check if it was here.

    def cancel_delivery(self): # This method is on SupplierDelivery
        if self.is_cancelled:
            return

        with transaction.atomic():
            # Reverse inventory transactions
            if hasattr(self, 'delivery_items'):
                for delivery_item in self.delivery_items.all():
                    InventoryTransaction.objects.create(
                        transaction_type='DELIVERY_REVERSAL',
                        motorcycle_model=delivery_item.motorcycle_model,
                        quantity=-delivery_item.delivered_quantity, # Negative quantity
                        reference_model='SupplierDelivery',
                        reference_id=self.id,
                        remarks=f"Reversal of delivery {self.delivery_reference}"
                    )
            
            self.is_cancelled = True
            self.save(update_fields=['is_cancelled', 'updated_at']) # This save will trigger payment status update
            
            # Explicitly update payment status after delivery cancellation
            # The save method of SupplierDelivery now handles this.
            # if self.payment:
            #    self.payment.update_completion_status(force_recalculate=True)


        self.is_cancelled = True
        self.save(update_fields=['is_cancelled']) # Only save the changed field for efficiency

    def save(self, *args, **kwargs):
        if not self.delivery_reference:
            timestamp = timezone.now().strftime('%Y%m%d%H%M%S%f')
            self.delivery_reference = f"DEL-{uuid.uuid4().hex[:8].upper()}-{timestamp}"
        
        is_new = self._state.adding
        old_is_cancelled = None
        if not is_new:
            original_delivery = SupplierDelivery.objects.get(pk=self.pk)
            old_is_cancelled = original_delivery.is_cancelled

        super().save(*args, **kwargs) # Save first
        
        if self.payment: # Then update payment status
            self.payment.update_completion_status(force_recalculate=True)
        
        # If cancellation status changed
        if not is_new and old_is_cancelled != self.is_cancelled:
            if self.payment:
                self.payment.update_completion_status(force_recalculate=True)

    def get_absolute_url(self):
        return reverse('delivery_detail', kwargs={'pk': self.pk})



class SupplierDeliveryItem(models.Model):
    """Details of each delivered motorcycle model"""
    delivery = models.ForeignKey(
        SupplierDelivery,
        on_delete=models.CASCADE,
        related_name='delivery_items'
    )
    motorcycle_model = models.ForeignKey(
        Motorcycle,
        on_delete=models.CASCADE,
        related_name='delivery_items'
    )
    delivered_quantity = models.PositiveIntegerField()
    delivery_remarks = models.TextField(blank=True)
    
    class Meta:
        unique_together = ['delivery', 'motorcycle_model']
    
    def __str__(self):
        return f"{self.motorcycle_model} - {self.delivered_quantity} delivered"
    
    def clean(self):
        """Validate delivery item data"""
        super().clean()

        if self.delivered_quantity is None or self.delivered_quantity <= 0:
            raise ValidationError("Delivered quantity must be greater than zero.")

        # --- Enhanced check for payment association ---
        if not hasattr(self, 'delivery') or self.delivery is None:
            raise ValidationError("Internal error: Delivery object not attached to item.")

        current_delivery_payment_id = getattr(self.delivery, 'payment_id', None)

        if not current_delivery_payment_id:
            raise ValidationError("Delivery is not associated with a payment.")
        try:
            current_payment = SupplierPayment.objects.get(id=current_delivery_payment_id)
        except SupplierPayment.DoesNotExist:
            raise ValidationError("Associated payment does not exist in the database.")
        except Exception as e:
            raise ValidationError(f"Error accessing associated payment: {e}")
        # --- End enhanced check ---

        # Check if delivery is cancelled
        if self.delivery.is_cancelled:
            raise ValidationError("Cannot add items to cancelled delivery.")
        
        # Validate against payment items
        try:
            payment_item = current_payment.payment_items.get(
                motorcycle_model=self.motorcycle_model
            )
            expected_qty = payment_item.expected_quantity if payment_item.expected_quantity is not None else 0

        except SupplierPaymentItem.DoesNotExist:
            raise ValidationError(
                f"Model {self.motorcycle_model} was not included in the original payment."
            )
        except Exception as e:
            raise ValidationError(f"Error validating against payment items: {e}")

        # Calculate total delivered for this model under this payment
        total_delivered_existing_in_db = SupplierDeliveryItem.objects.filter(
            delivery__payment=current_payment,
            motorcycle_model=self.motorcycle_model,
            delivery__is_cancelled=False
        ).exclude(pk=self.pk).aggregate(
            total=Sum('delivered_quantity')
        )['total'] or 0
        
        current_item_quantity = self.delivered_quantity

        if (total_delivered_existing_in_db + current_item_quantity) > expected_qty:
            raise ValidationError(
                f"Total delivered quantity ({total_delivered_existing_in_db + current_item_quantity}) "
                f"exceeds expected quantity ({expected_qty}) for model {self.motorcycle_model} "
                f"under payment {current_payment.payment_reference}."
            )
    
    def save(self, *args, **kwargs):
        self.full_clean() # Run validation
        super().save(*args, **kwargs)
        
        # Clear cached properties on the related payment
        if self.delivery and self.delivery.payment:
            self.delivery.payment.refresh_cached_properties()
            self.delivery.payment.update_completion_status(force_recalculate=True)

    def delete(self, *args, **kwargs):
        payment_to_update = None
        if self.delivery and self.delivery.payment:
            payment_to_update = self.delivery.payment
        
        super().delete(*args, **kwargs)
        
        if payment_to_update:
            payment_to_update.refresh_cached_properties()
            payment_to_update.update_completion_status(force_recalculate=True)


class InventoryTransaction(models.Model):
    """Immutable log of all inventory changes"""
    TRANSACTION_TYPES = [
        ('SUPPLIER_DELIVERY', 'Supplier Delivery'),
        ('DELIVERY_REVERSAL', 'Delivery Reversal'),
        ('SALE', 'Sale'),
        ('SALE_REVERSAL', 'Sale Reversal'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES)
    motorcycle_model = models.ForeignKey(
        Motorcycle,
        on_delete=models.CASCADE,
        related_name='inventory_transactions'
    )
    quantity = models.IntegerField(help_text="Positive for additions, negative for reductions")
    transaction_date = models.DateTimeField(auto_now_add=True)
    reference_model = models.CharField(
        max_length=50,
        help_text="Model name that triggered this transaction"
    )
    reference_id = models.PositiveIntegerField(
        help_text="ID of the record that triggered this transaction"
    )
    remarks = models.TextField(blank=True)
    
    class Meta:
        ordering = ['-transaction_date']
        indexes = [
            models.Index(fields=['motorcycle_model', 'transaction_date']),
            models.Index(fields=['reference_model', 'reference_id']),
        ]
    
    def __str__(self):
        sign = "+" if self.quantity >= 0 else ""
        return f"{self.motorcycle_model}: {sign}{self.quantity} ({self.transaction_type})"
    
    def clean(self):
        """Validate transaction data"""
        if self.quantity == 0:
            raise ValidationError("Transaction quantity cannot be zero")
    
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Inventory transactions are immutable and cannot be updated.")
        
        # For the first save (self._state.adding is True), proceed.
        self.full_clean() # It's good practice to call full_clean before super().save()
        super().save(*args, **kwargs)
    
    def delete(self, *args, **kwargs):
        raise ValidationError("Inventory transactions cannot be deleted")


class Inventory(models.Model):
    """Current stock levels by motorcycle model"""
    motorcycle_model = models.OneToOneField(
        Motorcycle,
        on_delete=models.CASCADE,
        related_name='inventory'
    )
    current_quantity = models.IntegerField(default=0)
    last_updated = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name_plural = "Inventories"
        ordering = ['motorcycle_model']
    
    def __str__(self):
        return f"{self.motorcycle_model}: {self.current_quantity} units"
    
    @classmethod
    def update_inventory(cls, motorcycle_model):
        """Update inventory quantity based on transactions."""
        total_quantity = InventoryTransaction.objects.filter(
            motorcycle_model=motorcycle_model
        ).aggregate(
            total=Sum('quantity')
        )['total'] or 0

        # Use QuerySet.update() for existing records to bypass instance save() validation.
        # This is the intended mechanism for updating inventory via transactions.
        updated_count = cls.objects.filter(motorcycle_model=motorcycle_model).update(
            current_quantity=total_quantity,
            last_updated=timezone.now() # auto_now=True on last_updated is for instance .save()
        )

        if updated_count == 0: # No existing record, so create one.
            inventory = cls.objects.create(
                motorcycle_model=motorcycle_model,
                current_quantity=total_quantity
                # last_updated will be set correctly by auto_now=True during this create/save
            )
            return inventory
        else: # Record was updated
            return cls.objects.get(motorcycle_model=motorcycle_model) # Return the updated instance
    
    def save(self, *args, **kwargs):
        # Prevent direct editing of current_quantity
        if self.pk:
            original = Inventory.objects.get(pk=self.pk)
            if original.current_quantity != self.current_quantity:
                raise ValidationError(
                    "Inventory quantity cannot be edited directly. "
                    "Use InventoryTransaction to make changes."
                )
        super().save(*args, **kwargs)
 

class Sale(models.Model):
    PAYMENT_TYPE_CHOICES = [
        ('DEPOSIT', 'Deposit'),
        ('LOAN', 'Loan'),
        ('CASH', 'Cash'),
        ('TRANSFER', 'Bank Transfer'),
    ]
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('CANCELLED', 'Cancelled'),
    ]

    customer = models.ForeignKey('Customer', on_delete=models.PROTECT, related_name='sales')
    motorcycle = models.ForeignKey('Motorcycle', on_delete=models.PROTECT, related_name='sales_records') # This is the motorcycle model/type
    sale_date = models.DateTimeField(default=timezone.now, help_text="Date of sale.") # Editable [cite: 1]
    
    # Non-editable after creation [cite: 1]
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPE_CHOICES)
    final_price = models.DecimalField(max_digits=12, decimal_places=2)
    
    # Editable on active sale [cite: 1]
    engine_no = models.CharField(max_length=100, unique=True, help_text="Unique engine number.")
    chassis_no = models.CharField(max_length=100, unique=True, help_text="Unique chassis number.")
    
    remarks = models.TextField(blank=True, null=True, help_text="Optional remarks about the sale.") # Editable [cite: 1]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    
    sale_reference = models.CharField(max_length=50, unique=True, blank=True, help_text="Auto-generated unique sale reference.")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-sale_date', '-created_at']
        verbose_name = "Sale Record"
        verbose_name_plural = "Sale Records"

    def __str__(self):
        ref = self.sale_reference or f"Sale Draft (ID: {self.pk})"
        return f"{ref} - {self.customer.name if self.customer else 'N/A'} - {self.motorcycle.name if self.motorcycle else 'N/A'}"

    def get_absolute_url(self):
         return reverse('sale_detail', kwargs={'pk': self.pk}) # Assuming you'll have a 'sale_detail' URL name

    def clean(self):
        super().clean()
        if self.final_price is not None and self.final_price <= Decimal('0.00'):
            raise ValidationError({'final_price': "Final price must be greater than zero."})
        
        if not self.engine_no: # Required field
            raise ValidationError({'engine_no': "Engine number cannot be blank."})
        if not self.chassis_no: # Required field
            raise ValidationError({'chassis_no': "Chassis number cannot be blank."})

        # Validations for updates
        if self.pk:
            original = Sale.objects.get(pk=self.pk)
            if original.status == self.STATUS_CHOICES[1][0]: # CANCELLED
                if self.status == self.STATUS_CHOICES[0][0]: # ACTIVE
                     raise ValidationError("Cancelled sales cannot be reactivated. Please create a new sale.")
                if (original.engine_no != self.engine_no or
                        original.chassis_no != self.chassis_no or
                        original.sale_date != self.sale_date):
                    raise ValidationError("Cannot modify engine/chassis number or sale date for a cancelled sale. Remarks may be an exception.")

            # Prevent editing of restricted fields after creation [cite: 1]
            restricted_fields = ['customer', 'motorcycle', 'payment_type', 'final_price']
            for field_name in restricted_fields:
                if getattr(self, field_name) != getattr(original, field_name):
                    # This check is primarily a safeguard; form logic should prevent these edits.
                    raise ValidationError({
                        field_name: f"{field_name.replace('_', ' ').title()} cannot be changed after creation. "
                                    f"Please cancel this sale and create a new one if these details need to change."
                    })
    

class Deposit(models.Model):
    DEPOSIT_STATUS_CHOICES = [
        ('active', 'Active'),
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]

    DEPOSIT_TYPE_CHOICES = [
        ('normal', 'Normal'),
        ('purchase', 'Purchase'),
    ]
    
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    deposit_amount = models.DecimalField(max_digits=10, decimal_places=2)
    deposit_date = models.DateTimeField(default=timezone.now)
    deposit_type = models.CharField(
        max_length=50,
        choices=DEPOSIT_TYPE_CHOICES,
        default='normal'
    )
    transaction_note = models.CharField(max_length=250, null=True, blank=True)
    deposit_reference = models.CharField(max_length=100, unique=True, blank=True)
    deposit_status = models.CharField(
        max_length=20, 
        choices=DEPOSIT_STATUS_CHOICES, 
        default='active'
    )

    def __str__(self):
        ref = self.deposit_reference if self.deposit_reference else f"DEP-{self.id}"
        return f"{ref} - {self.customer} - ₦{self.deposit_amount} on {self.deposit_date.strftime('%Y-%m-%d')}"

    def clean(self):
        if self.deposit_amount is not None and self.deposit_amount <= 0:
            raise ValidationError("Deposit amount must be greater than zero.")

    def clear_withdrawal_cache(self):
        """Clear the cached total withdrawn amount"""
        if hasattr(self, '_cached_total_withdrawn'):
            delattr(self, '_cached_total_withdrawn')

    def get_total_withdrawn(self, force_refresh=False):
        """Get total withdrawn amount with optional cache refresh"""
        # Clear cache if force refresh is requested
        if force_refresh:
            self.clear_withdrawal_cache()
            
        # Check if we've already computed and cached this for this instance
        if not hasattr(self, '_cached_total_withdrawn'):
            total = self.withdrawal_set.filter(
                withdrawal_status='completed'
            ).aggregate(total=Sum('withdrawal_amount'))['total'] or Decimal('0.00')
            self._cached_total_withdrawn = total
        return self._cached_total_withdrawn

    @property
    def remaining_balance(self):
        """Get remaining balance (always fresh calculation)"""
        # Always get fresh total for balance calculation
        return self.deposit_amount - self.get_total_withdrawn(force_refresh=True)

    def update_status_based_on_withdrawals(self):
        """
        Update deposit status based on withdrawal amounts.
        This method is the core of the auto-status update functionality.
        """
        # Skip status update if deposit is cancelled
        if self.deposit_status == 'cancelled':
            return False
        
        # Always get fresh total withdrawn amount
        total_withdrawn = self.get_total_withdrawn(force_refresh=True)
        old_status = self.deposit_status
        
        # Determine new status based on withdrawal amount
        if total_withdrawn >= self.deposit_amount:
            new_status = 'completed'
        else:
            new_status = 'active'
        
        # Update status if it has changed
        if old_status != new_status:
            self.deposit_status = new_status
            
            # Add note about automatic status change
            if self.deposit_status == 'completed':
                timestamp = timezone.now().strftime('%Y-%m-%d %H:%M')
                status_note = f"This deposit has been fully withdrawn on {timestamp}"
                self.transaction_note = status_note.strip()
            
            self.save(update_fields=['deposit_status', 'transaction_note'])
            return True
        
        return False

    @classmethod
    def sync_all_deposit_statuses(cls):
        """
        Utility method to sync all deposit statuses.
        Useful for data migrations or bulk corrections.
        """
        updated_count = 0
        for deposit in cls.objects.exclude(deposit_status='cancelled'):
            if deposit.update_status_based_on_withdrawals():
                updated_count += 1
        return updated_count

    def save(self, *args, **kwargs):
        # Generate deposit reference for new instances
        if not self.pk and not self.deposit_reference:
            today_date_str = timezone.now().strftime('%Y%m%d')
            prefix = f'DEP-{today_date_str}-'
            
            with transaction.atomic():
                last_deposit_with_prefix = Deposit.objects.filter(
                    deposit_reference__startswith=prefix
                ).order_by('-deposit_reference').first()

                next_sequence = 1
                if last_deposit_with_prefix:
                    try:
                        last_sequence_str = last_deposit_with_prefix.deposit_reference.split('-')[-1]
                        last_sequence = int(last_sequence_str)
                        next_sequence = last_sequence + 1
                    except (ValueError, IndexError):
                        next_sequence = 1
                
                self.deposit_reference = f"{prefix}{next_sequence:04d}"

        # Clear cache before saving
        self.clear_withdrawal_cache()
        
        self.full_clean()
        super().save(*args, **kwargs)
    
    def get_absolute_url(self):
        return reverse('deposit_detail', kwargs={'pk': self.pk})


class Withdrawal(models.Model):
    WITHDRAWAL_STATUS_CHOICES = [
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    deposit = models.ForeignKey(Deposit, on_delete=models.CASCADE)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_withdrawal')
    withdrawal_amount = models.DecimalField(max_digits=10, decimal_places=2, blank=False)
    withdrawal_date = models.DateTimeField(default=timezone.now)
    remarks = models.CharField(max_length=250, null=True, blank=True)
    withdrawal_status = models.CharField(
        max_length=20, 
        choices=WITHDRAWAL_STATUS_CHOICES, 
        default='completed'
    )

    def __str__(self):
        ref = self.deposit.deposit_reference if self.deposit else "N/A"
        return f"{ref} - {self.deposit.customer} - ₦{self.withdrawal_amount} on {self.withdrawal_date.strftime('%Y-%m-%d')}"
        
    def clean(self):
        if self.withdrawal_amount is not None and self.withdrawal_amount <= 0:
            raise ValidationError("Withdrawal amount must be greater than zero.")

    def save(self, *args, **kwargs):
        """Enhanced save method that triggers deposit status update"""
        # Store the old values for comparison (in case of updates)
        old_deposit_id = None
        old_status = None
        old_amount = None
        
        if self.pk:
            try:
                old_withdrawal = Withdrawal.objects.get(pk=self.pk)
                old_deposit_id = old_withdrawal.deposit_id
                old_status = old_withdrawal.withdrawal_status
                old_amount = old_withdrawal.withdrawal_amount
            except Withdrawal.DoesNotExist:
                pass
        
        # Clear the deposit's withdrawal cache before saving
        if self.deposit:
            self.deposit.clear_withdrawal_cache()
        
        # Save the withdrawal
        super().save(*args, **kwargs)
        
        # Update the current deposit status
        if self.deposit:
            self.deposit.update_status_based_on_withdrawals()
        
        # If deposit changed (rare case), update the old deposit too
        if old_deposit_id and old_deposit_id != self.deposit_id:
            try:
                old_deposit = Deposit.objects.get(pk=old_deposit_id)
                old_deposit.clear_withdrawal_cache()
                old_deposit.update_status_based_on_withdrawals()
            except Deposit.DoesNotExist:
                pass

    def delete(self, *args, **kwargs):
        """Enhanced delete method that triggers deposit status update"""
        deposit = self.deposit
        
        # Clear the deposit's withdrawal cache before deletion
        if deposit:
            deposit.clear_withdrawal_cache()
            
        super().delete(*args, **kwargs)
        
        # Update deposit status after deletion
        if deposit:
            deposit.update_status_based_on_withdrawals()

    def get_absolute_url(self):
        return reverse('withdrawal_detail', kwargs={'pk': self.pk})
    
    @classmethod
    def get_customer_balance(cls, customer):
        """Calculate current balance for a customer"""
        from django.db.models import Sum
        
        total_deposits = Deposit.objects.filter(
            customer=customer, 
            deposit_status__in=['active', 'completed']
        ).aggregate(total=Sum('deposit_amount'))['total'] or 0
        
        # Get withdrawals from all deposits of this customer
        total_withdrawals = cls.objects.filter(
            deposit__customer=customer, 
            withdrawal_status='completed'
        ).aggregate(total=Sum('withdrawal_amount'))['total'] or 0
        
        return total_deposits - total_withdrawals


class Withdrawal(models.Model):
    WITHDRAWAL_STATUS_CHOICES = [
        ('completed', 'Completed'),
        ('cancelled', 'Cancelled'),
    ]
    deposit = models.ForeignKey(Deposit, on_delete=models.CASCADE)
    sale = models.ForeignKey(Sale, on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_withdrawal')
    withdrawal_amount = models.DecimalField(max_digits=10, decimal_places=2, blank=False)
    withdrawal_date = models.DateTimeField(default=timezone.now)
    remarks = models.CharField(max_length=250, null=True, blank=True)
    withdrawal_status = models.CharField(
        max_length=20, 
        choices=WITHDRAWAL_STATUS_CHOICES, 
        default='completed'
    )

    def __str__(self):
        ref = self.deposit.deposit_reference if self.deposit else "N/A"
        return f"{ref} - {self.deposit.customer} - ₦{self.withdrawal_amount} on {self.withdrawal_date.strftime('%Y-%m-%d')}"
        
    def clean(self):
        if self.withdrawal_amount is not None and self.withdrawal_amount <= 0:
            raise ValidationError("Withdrawal amount must be greater than zero.")

    def save(self, *args, **kwargs):
        """Enhanced save method that triggers deposit status update"""
        # Store the old values for comparison (in case of updates)
        old_deposit_id = None
        old_status = None
        old_amount = None
        
        if self.pk:
            try:
                old_withdrawal = Withdrawal.objects.get(pk=self.pk)
                old_deposit_id = old_withdrawal.deposit_id
                old_status = old_withdrawal.withdrawal_status
                old_amount = old_withdrawal.withdrawal_amount
            except Withdrawal.DoesNotExist:
                pass
        
        # Save the withdrawal
        super().save(*args, **kwargs)
        
        # Update the current deposit status
        if self.deposit:
            self.deposit.update_status_based_on_withdrawals()
        
        # If deposit changed (rare case), update the old deposit too
        if old_deposit_id and old_deposit_id != self.deposit_id:
            try:
                old_deposit = Deposit.objects.get(pk=old_deposit_id)
                old_deposit.update_status_based_on_withdrawals()
            except Deposit.DoesNotExist:
                pass

    def delete(self, *args, **kwargs):
        """Enhanced delete method that triggers deposit status update"""
        deposit = self.deposit
        super().delete(*args, **kwargs)
        
        # Update deposit status after deletion
        if deposit:
            deposit.update_status_based_on_withdrawals()

    def get_absolute_url(self): # New method
        return reverse('withdrawal_detail', kwargs={'pk': self.pk})
    
    @classmethod
    def get_customer_balance(cls, customer):
        """Calculate current balance for a customer"""
        from django.db.models import Sum
        
        total_deposits = Deposit.objects.filter(
            customer=customer, 
            deposit_status__in=['active', 'completed']  # Include completed deposits in balance
        ).aggregate(total=Sum('deposit_amount'))['total'] or 0
        
        # Get withdrawals from all deposits of this customer
        total_withdrawals = cls.objects.filter(
            deposit__customer=customer, 
            withdrawal_status='completed'
        ).aggregate(total=Sum('withdrawal_amount'))['total'] or 0
        
        return total_deposits - total_withdrawals
 

class Loan(models.Model):
    LOAN_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('repaid', 'Repaid'),
        ('partially repaid', 'Partially Repaid'),
        ('cancelled', 'Cancelled'),
    ]

    customer = models.ForeignKey('Customer', on_delete=models.PROTECT)
    sale = models.ForeignKey('Sale', on_delete=models.SET_NULL, null=True, blank=True, related_name='sale_loan')
    loan_amount = models.DecimalField(max_digits=10, decimal_places=2)
    loan_date = models.DateTimeField(default=timezone.now)
    balance = models.DecimalField(max_digits=10, decimal_places=2) # Initialized in save
    loan_status = models.CharField(max_length=20, choices=LOAN_STATUS_CHOICES, default='pending')
    remarks = models.CharField(max_length=250, null=True, blank=True)
    loan_reference = models.CharField(max_length=100, unique=True, blank=True) # Generated in save

    # Add created_at and updated_at if not in a base model
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta: # Add Meta if you want ordering
        ordering = ['-loan_date', '-created_at']

    def __str__(self): # Your existing __str__
        return f"Loan {self.loan_reference if self.loan_reference else self.id} - {self.customer.name} (Status: {self.get_loan_status_display()})"

    def save(self, *args, **kwargs): # Your existing save logic
        if not self.pk: # New loan
            self.balance = self.loan_amount
            if not self.loan_reference:
                today_date_str = timezone.now().strftime('%Y%m%d')
                prefix = f'LOAN-{today_date_str}-'
                # Using atomic transaction for fetching last sequence number for robustness
                with transaction.atomic(): # Added for sequence generation safety
                    last_loan_with_prefix = Loan.objects.filter(
                        loan_reference__startswith=prefix
                    ).order_by('-loan_reference').first()
                    next_sequence = 1
                    if last_loan_with_prefix and last_loan_with_prefix.loan_reference:
                        try:
                            last_sequence_str = last_loan_with_prefix.loan_reference.split('-')[-1]
                            last_sequence = int(last_sequence_str)
                            next_sequence = last_sequence + 1
                        except (ValueError, IndexError):
                            next_sequence = 1
                    self.loan_reference = f"{prefix}{next_sequence:04d}"
        else: # Existing loan being updated
            try:
                original_loan = Loan.objects.get(pk=self.pk)
                if original_loan.loan_amount != self.loan_amount and self.loan_status != 'cancelled':
                    amount_repaid = original_loan.loan_amount - original_loan.balance
                    new_balance = self.loan_amount - amount_repaid
                    self.balance = max(Decimal('0.00'), new_balance)
                    if self.balance <= Decimal('0.00'):
                        self.loan_status = 'repaid'
                        self.balance = Decimal('0.00')
                    elif self.balance < self.loan_amount:
                        self.loan_status = 'partially repaid'
                    else: # balance >= loan_amount
                        self.loan_status = 'pending'
            except Loan.DoesNotExist:
                pass
        super().save(*args, **kwargs)

    def update_balance(self, repayment_amount): # Your existing method
        """Called when a repayment is made or deleted."""
        self.balance = self.balance - repayment_amount # Subtract for new repayment, add back for deleted repayment
        self.balance = max(Decimal('0.00'), self.balance) # Ensure balance doesn't go negative below zero

        if self.balance <= Decimal('0.00'):
            self.loan_status = 'repaid'
            self.balance = Decimal('0.00') # Normalize to 0 if fully paid or overpaid
        elif self.balance < self.loan_amount:
            self.loan_status = 'partially repaid'
        else: # balance == loan_amount (e.g. a repayment was deleted fully reversing it)
            self.loan_status = 'pending'
        self.save(update_fields=['balance', 'loan_status', 'updated_at'])


    def get_absolute_url(self):
        return reverse('loan_detail', kwargs={'pk': self.pk})


class LoanRepayment(models.Model):
    loan = models.ForeignKey(Loan, on_delete=models.PROTECT) # PROTECT is good
    repayment_date = models.DateTimeField(default=timezone.now)
    repayment_amount = models.DecimalField(max_digits=10, decimal_places=2)
    remarks = models.CharField(max_length=250, null=True, blank=True)
    # No status field currently, deletion is hard delete.

    # Add created_at and updated_at if not in a base model
    created_at = models.DateTimeField(auto_now_add=True, null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True, null=True, blank=True)

    class Meta: # Add Meta if you want ordering
        ordering = ['-repayment_date', '-created_at']

    def __str__(self): # Your existing __str__
        loan_ref = self.loan.loan_reference if self.loan else "N/A"
        customer_name = self.loan.customer.name if self.loan and self.loan.customer else "N/A"
        return f"Repayment of ₦{self.repayment_amount} for Loan {loan_ref} by {customer_name} on {self.repayment_date.strftime('%Y-%m-%d')}"

    def clean(self): # Add basic validation
        super().clean()
        if self.repayment_amount is not None and self.repayment_amount <= Decimal('0.00'):
            raise ValidationError({'repayment_amount': "Repayment amount must be greater than zero."})

    def get_absolute_url(self):
        return reverse('loan_repayment_detail', kwargs={'pk': self.pk})