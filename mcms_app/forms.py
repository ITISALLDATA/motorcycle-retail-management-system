from .models import *
from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone
from django.forms import inlineformset_factory
from django.db.models import Sum, F, Q
from decimal import Decimal
from django.forms.models import BaseInlineFormSet
from decimal import Decimal, InvalidOperation
from django.urls import reverse
import datetime



class SupplierForm(forms.ModelForm):
    """Form for creating and editing suppliers"""
    
    class Meta:
        model = Supplier
        fields = ['name', 'phone', 'address']
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'required': True}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }


class SupplierFilterForm(forms.Form):
    """Form for filtering supplier listings"""
    name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Supplier Name'})
    )
    phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Phone Number'})
    )

class SupplierPaymentForm(forms.ModelForm):
    """Form for creating supplier payments"""

    class Meta:
        model = SupplierPayment
        fields = [
            'supplier', 'amount_paid',
            'payment_date', 'payment_method', 'remarks'
        ]
        widgets = {
            'supplier': forms.Select(attrs={'class': 'form-control', 'required': True}),
            'amount_paid': forms.NumberInput(attrs={
                'class': 'form-control',
                'required': True,
                'step': '0.01',
                'min': '0.01'
            }),
            'payment_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
                'required': True
            }),
            'payment_method': forms.Select(attrs={'class': 'form-control'}),
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields['amount_paid'].widget.attrs['min'] = '200000.00'
        self.fields['supplier'].queryset = Supplier.objects.all()
        self.fields['payment_date'].widget.attrs['max'] = (timezone.now().date() + timezone.timedelta(days=365)).isoformat()

        if self.instance and self.instance.pk: # If editing an existing payment
            if not self.instance.is_editable: # is_editable combines status and has_deliveries
                for field_name in self.fields:
                    self.fields[field_name].disabled = True
                if self.instance.status == SupplierPayment.COMPLETED:
                    self.add_error(None, "This payment is COMPLETED and cannot be edited.")
                elif self.instance.status == SupplierPayment.CANCELLED:
                    self.add_error(None, "This payment is CANCELLED and cannot be edited.")
                elif self.instance.has_deliveries: # Should be covered by is_editable
                     self.add_error(None, "This payment cannot be edited because deliveries have already been made.")

    def clean_amount_paid(self):
        amount = self.cleaned_data.get('amount_paid')
        if amount is not None:
            if amount <= Decimal('0.00'):
                raise ValidationError("Payment amount must be greater than zero.")
            # Rule: Amount_paid should not be below N200K
            if amount < Decimal('200000.00'):
                raise ValidationError("Minimum payment amount is ₦200,000.00.")
        return amount

    def clean_payment_date(self):
        date = self.cleaned_data.get('payment_date')
        if date:
            today = timezone.now().date()
            one_year_future = today + timezone.timedelta(days=365)

            # 🔁 Convert `date` to just a date if it's a datetime
            if isinstance(date, datetime.datetime):
                date = date.date()

            if date > one_year_future:
                raise ValidationError("Payment date cannot be more than one year in the future.")

            if not self.instance.pk and date < today:
                raise ValidationError("Payment date cannot be in the past for new payments.")
            
        if date and isinstance(date, datetime.datetime) and timezone.is_naive(date):
            return timezone.make_aware(date, timezone.get_default_timezone())
        return date
        
    def clean(self):
        cleaned_data = super().clean()
        # For edit scenarios, prevent changes if not editable
        if self.instance and self.instance.pk and not self.instance.is_editable and self.has_changed():
            raise ValidationError(f"This payment (Status: {self.instance.get_status_display()}) cannot be edited at this stage.")
        return cleaned_data


class SupplierPaymentItemForm(forms.ModelForm):
    """Form for individual payment items"""
    
    class Meta:
        model = SupplierPaymentItem
        fields = ['motorcycle_model', 'expected_quantity', 'unit_price', 'remarks']
        widgets = {
            'motorcycle_model': forms.Select(attrs={
                'class': 'form-control motorcycle-select',
                'required': True
            }),
            'expected_quantity': forms.NumberInput(attrs={
                'class': 'form-control quantity-input',
                'required': True,
                'min': '1'
            }),
            'unit_price': forms.NumberInput(attrs={
                'class': 'form-control price-input',
                'required': True,
                'step': '0.01',
                'min': '0.01'
            }),
            'remarks': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['motorcycle_model'].queryset = Motorcycle.objects.all()

        # If payment is not ACTIVE, make fields read-only
        if hasattr(self.instance, 'payment') and self.instance.payment:
            if self.instance.payment.status != SupplierPayment.ACTIVE or \
               (self.instance.payment.status == SupplierPayment.ACTIVE and self.instance.payment.has_deliveries):
                for field_name in self.fields:
                    self.fields[field_name].disabled = True

    def clean_expected_quantity(self): # Point 4 (server-side)
        quantity = self.cleaned_data.get('expected_quantity')
        if quantity is not None and quantity <= 0:
            raise ValidationError("Expected quantity must be greater than zero.")
        return quantity

    def clean_unit_price(self): # Point 4 (server-side)
        price = self.cleaned_data.get('unit_price')
        if price is not None and price <= Decimal('0.00'):
            raise ValidationError("Unit price must be greater than zero.")
        return price

    def clean(self): # Clarified Point 3
        cleaned_data = super().clean()
        expected_quantity = cleaned_data.get('expected_quantity')
        unit_price = cleaned_data.get('unit_price')
        
        # Access parent_payment_amount_paid passed from the view/formset factory
        # This is crucial for validating against the parent payment's amount.
        parent_amount_paid = self.parent_payment_amount_paid

        if parent_amount_paid is None and self.prefix: # If in a formset, try to get from formset's instance
            formset_instance = getattr(self, 'formset_parent_instance', None) # Assume view sets this on each form
            if formset_instance and hasattr(formset_instance, 'amount_paid'):
                 parent_amount_paid = formset_instance.amount_paid


        if expected_quantity is not None and unit_price is not None and parent_amount_paid is not None:
            item_total_value = Decimal(str(expected_quantity)) * unit_price
            if item_total_value > parent_amount_paid:
                raise ValidationError(
                    f"The value of this single item (₦{item_total_value:,.2f}) "
                    f"exceeds the total Amount Paid for this payment (₦{parent_amount_paid:,.2f})."
                )
        return cleaned_data
    

class BaseSupplierPaymentItemFormSet(BaseInlineFormSet):
    def __init__(self, *args, **kwargs):
        # Pop the custom kwarg before super().__init__
        self.parent_amount_paid_for_items = kwargs.pop('parent_amount_paid_for_items', None)
        super().__init__(*args, **kwargs)
        
        # Make parent_amount_paid_for_items and the formset's parent instance (SupplierPayment)
        # available to each form instance (SupplierPaymentItemForm) within this formset.
        for form in self.forms:
            form.parent_payment_amount_paid = self.parent_amount_paid_for_items
            form.formset_parent_instance = self.instance # self.instance is the parent SupplierPayment

    def clean(self):
        super().clean() # Call the parent's clean method first.

        # Don't bother with further validation if individual forms already have errors.
        if any(self.errors):
            return

        total_items_value = Decimal('0.00')
        active_forms_count = 0
        motorcycle_models_seen = set()

        for form in self.forms:
            # Skip forms that are marked for deletion or don't have cleaned_data (e.g., empty extra forms)
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if self.can_delete and self._should_delete_form(form):
                continue
            
            active_forms_count += 1
            
            # Validation 1: No duplicate payment items (within the current submission)
            motorcycle_model = form.cleaned_data.get('motorcycle_model')
            if motorcycle_model:
                if motorcycle_model in motorcycle_models_seen:
                    # Add error to the specific form's field that caused the duplication.
                    form.add_error('motorcycle_model', 'Duplicate motorcycle model in payment items. Each item must be unique.')
                motorcycle_models_seen.add(motorcycle_model)

            # Accumulate total value from valid items
            expected_quantity = form.cleaned_data.get('expected_quantity', 0) # Default to 0 if not present
            unit_price = form.cleaned_data.get('unit_price', Decimal('0.00'))   # Default to 0.00 if not present

            # Ensure quantities and prices are positive before including in sum (form-level validation should catch <=0)
            if expected_quantity > 0 and unit_price > Decimal('0.00'):
                total_items_value += (Decimal(str(expected_quantity)) * unit_price)

        # Determine the definitive 'amount_paid' to use for validation.
        # Priority is given to 'parent_amount_paid_for_items' passed from the view,
        # as this should be the cleaned value from the parent form's current submission.
        current_amount_paid_for_validation = self.parent_amount_paid_for_items

        # Fallback: If not explicitly passed (e.g., in some other unforeseen workflow or if view logic misses it),
        # try to use the amount_paid from the formset's parent instance.
        # self.instance is the SupplierPayment object. If the parent form (SupplierPaymentForm)
        # was validated before this formset, self.instance.amount_paid would be the cleaned value.
        if current_amount_paid_for_validation is None and self.instance and hasattr(self.instance, 'amount_paid'):
            current_amount_paid_for_validation = self.instance.amount_paid
        
        # Now, perform validations that depend on current_amount_paid_for_validation

        if current_amount_paid_for_validation is not None:
            # Validation 2: Total item value MUST EQUAL amount_paid
            if active_forms_count > 0: # Only if there are items to sum up
                if total_items_value != current_amount_paid_for_validation:
                    difference = current_amount_paid_for_validation - total_items_value
                    error_message = (
                        f"The total value of all items (₦{total_items_value:,.2f}) "
                        f"must be equal to the Amount Paid (₦{current_amount_paid_for_validation:,.2f}). "
                        f"Difference: ₦{difference:,.2f}."
                    )
                    # This error applies to the formset as a whole.
                    raise ValidationError(error_message, code='total_mismatch')
            
            # Additional Validation: If amount_paid > 0, at least one item must be specified.
            elif active_forms_count == 0 and current_amount_paid_for_validation > Decimal('0.00'):
                raise ValidationError(
                    "If Amount Paid is greater than zero (₦{:.2f}), at least one payment item must be specified.".format(current_amount_paid_for_validation),
                    code='no_items_for_payment'
                )
        elif active_forms_count > 0: 
            # This case means there are items, but we couldn't determine the parent payment's amount_paid.
            # This would typically indicate an issue in the view logic passing data to the formset.
            raise ValidationError(
                "Cannot validate item totals: The payment's Amount Paid is not available to the item formset for comparison.",
                code='missing_amount_paid_for_validation'
            )


class SupplierPaymentItemFormSetHelper:
    def get_formset(self, data=None, instance=None, parent_amount_paid_for_items=None):
        # This helper function should be how you get your formset in the view.
        # The view needs to pass `parent_amount_paid_for_items` during new payment creation.
        formset_kwargs = {'form_kwargs': {}}
        if parent_amount_paid_for_items is not None:
            # This is a way to pass extra kwargs to each form instance,
            # but our BaseFormSet constructor directly accepts it now.
            # formset_kwargs['form_kwargs']['parent_payment_amount_paid'] = parent_amount_paid_for_items
            formset_kwargs['parent_amount_paid_for_items'] = parent_amount_paid_for_items


        PaymentItemFormSet = forms.inlineformset_factory(
            SupplierPayment,
            SupplierPaymentItem,
            form=SupplierPaymentItemForm,
            formset=BaseSupplierPaymentItemFormSet, # Use our custom base formset
            fields=['motorcycle_model', 'expected_quantity', 'unit_price', 'remarks'],
            extra=1,
            can_delete=True,
            # min_num=0, # Allow zero forms if payment amount might be zero or items optional initially
            # validate_min=False,
        )
        return PaymentItemFormSet(data=data, instance=instance, prefix='items', **formset_kwargs) # Added prefix


class PaymentFilterForm(forms.Form):
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        required=False,
        empty_label="All Suppliers",
        widget=forms.Select(attrs={'class': 'form-control '})
    )
    payment_method = forms.ChoiceField(
        choices=[('', 'All Methods')] + SupplierPayment._meta.get_field('payment_method').choices,
        required=False,
        widget=forms.Select(attrs={'class': 'form-control '})
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control ', 'type': 'date'})
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    status = forms.ChoiceField( # Changed from show_cancelled
        choices=[('', 'All Statuses')] + SupplierPayment.STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-control'})
    )


class SupplierDeliveryForm(forms.ModelForm):
    """Form for creating supplier deliveries"""

    class Meta:
        model = SupplierDelivery
        # Removed 'delivery_reference' from fields entirely
        fields = ['payment', 'delivery_date', 'remarks']
        widgets = {
            'payment': forms.Select(attrs={'class': 'form-control', 'required': True}),
            'delivery_date': forms.DateInput(attrs={
                'class': 'form-control',
                'type': 'date',
                'required': True
            }),
            'remarks': forms.TextInput(attrs={'class': 'form-control'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Filter payments to only show ACTIVE ones that are not fully delivered yet
        available_payments_queryset = SupplierPayment.objects.filter(
            status=SupplierPayment.ACTIVE, # Only ACTIVE payments
            payment_items__isnull=False
        ).annotate(
            annotated_total_expected_quantity=Coalesce(
                Sum('payment_items__expected_quantity'), 0, output_field=DecimalField()
            ),
            annotated_total_delivered_quantity=Coalesce(
                Sum('deliveries__delivery_items__delivered_quantity', filter=Q(deliveries__is_cancelled=False)),
                0, output_field=DecimalField()
            )
        ).filter(
            # Total delivered < total expected
            Q(annotated_total_delivered_quantity__lt=F('annotated_total_expected_quantity')) |
            # Or no items expected (should ideally not happen if payment_items__isnull=False is strict)
            Q(annotated_total_expected_quantity=Decimal('0.00')) 
        ).select_related('supplier').distinct().order_by('payment_reference')
        
        # If editing an existing delivery, ensure its payment is in the queryset
        if self.instance and self.instance.pk and self.instance.payment:
            current_payment_qs = SupplierPayment.objects.filter(pk=self.instance.payment.pk)
            self.fields['payment'].queryset = (available_payments_queryset | current_payment_qs).distinct().order_by('payment_reference')
        else:
            self.fields['payment'].queryset = available_payments_queryset
        # ...
        # Disable fields if delivery is cancelled
        if self.instance.pk and self.instance.is_cancelled:
            for field_name in self.fields:
                self.fields[field_name].disabled = True
            self.add_error(None, "This delivery is CANCELLED and cannot be edited.")


    def clean(self):
        cleaned_data = super().clean()
        payment = cleaned_data.get('payment')

        if self.instance.pk and self.instance.is_cancelled and self.has_changed():
            raise ValidationError("This delivery is CANCELLED and cannot be edited.")

        if payment:
            if payment.status == SupplierPayment.CANCELLED:
                self.add_error('payment', "Cannot create delivery for a CANCELLED payment.")
            elif payment.status == SupplierPayment.COMPLETED:
                 # Check if this delivery is trying to add more than what made it complete initially (edge case)
                if not self.instance or not self.instance.pk: # For new deliveries
                    self.add_error('payment', f"Payment {payment.payment_reference} is already COMPLETED. No new deliveries can be added.")
            if not payment.payment_items.exists():
                self.add_error('payment', "Cannot create delivery for payment with no items.")
        return cleaned_data


class SupplierDeliveryItemForm(forms.ModelForm):
    """Form for individual delivery items"""
    
    class Meta:
        model = SupplierDeliveryItem
        fields = ['motorcycle_model', 'delivered_quantity', 'delivery_remarks']
        widgets = {
            'motorcycle_model': forms.Select(attrs={
                'class': 'form-control delivery-motorcycle-select',
                'required': True
            }),
            'delivered_quantity': forms.NumberInput(attrs={
                'class': 'form-control delivered-quantity-input',
                'required': True,
                'min': '1'
            }),
            'delivery_remarks': forms.TextInput(attrs={'class': 'form-control'}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # If delivery is set, limit motorcycle models to those in the payment
        if hasattr(self.instance, 'delivery') and self.instance.delivery:
            payment_items = self.instance.delivery.payment.payment_items.all()
            self.fields['motorcycle_model'].queryset = Motorcycle.objects.filter(
                payment_items__in=payment_items
            ).distinct()
        else:
            self.fields['motorcycle_model'].queryset = Motorcycle.objects.all()


# Create formset for delivery items
SupplierDeliveryItemFormSet = inlineformset_factory(
    SupplierDelivery,
    SupplierDeliveryItem,
    form=SupplierDeliveryItemForm,
    extra=1,
    can_delete=True,
    min_num=1,
    validate_min=True,
)


class SupplierDeliveryItemFormSetHelper:
    """Helper class for delivery item formset with validation"""
    
    def __init__(self, formset_class=SupplierDeliveryItemFormSet):
            self.formset_class = formset_class
    
    def get_formset(self, data=None, instance=None):
        """Get formset with custom validation"""
        
        class ValidatedDeliveryFormSet(self.formset_class):
            def clean(self):
                """Validate the entire delivery formset"""
                super().clean() # Call the base formset's clean method first

                forms_to_process = [form for form in self.forms if form.cleaned_data and not form.cleaned_data.get('DELETE', False)]

                # Validation 1: At least one item required (Formset non-form error)
                if not forms_to_process:
                    raise ValidationError("At least one delivery item is required")
                
                # Validation 2: Check for duplicate motorcycle models within the formset (Field-specific error)
                motorcycle_models_seen = {} # Use dict to track form index for error
                
                for i, form in enumerate(forms_to_process):
                    motorcycle_model = form.cleaned_data.get('motorcycle_model')
                    delivered_quantity = form.cleaned_data.get('delivered_quantity')
                    
                    if motorcycle_model: # Only validate if a model was selected
                        if motorcycle_model in motorcycle_models_seen:
                            # Add error to the current form's field
                            form.add_error('motorcycle_model', f"Duplicate motorcycle model: {motorcycle_model}")
                            # Also add error to the previously seen form's field if it's the first duplicate instance
                            # (This requires mapping back to the original form index)
                            # This part is complex for a simple add_error, often a formset.non_form_error is simpler for duplicates
                            # However, to be field-specific, we'll just add to the current one.
                            # Alternatively, raise a formset-wide error if you prefer:
                            # raise ValidationError(f"Duplicate motorcycle model in delivery: {motorcycle_model}")
                        motorcycle_models_seen[motorcycle_model] = i # Store index for later use if needed
                        
                        # Validation 3: Validate that motorcycle model was in the original payment (Field-specific error)
                        if self.instance and self.instance.pk and self.instance.payment:
                            try:
                                payment_item = self.instance.payment.payment_items.get(motorcycle_model=motorcycle_model)
                            except SupplierPaymentItem.DoesNotExist:
                                form.add_error('motorcycle_model',
                                    f"'{motorcycle_model}' was not included in the original payment."
                                )
                                continue # Skip further quantity validation for this item if model is invalid
                            
                            # Validation 4: Check if total delivered quantity exceeds expected quantity (Field-specific error)
                            if delivered_quantity is not None and delivered_quantity > 0: # Ensure quantity is valid and positive
                                # Exclude the current item's quantity if it's an existing instance being edited
                                total_delivered_existing_in_db = SupplierDeliveryItem.objects.filter(
                                    delivery__payment=self.instance.payment,
                                    motorcycle_model=motorcycle_model,
                                    delivery__is_cancelled=False
                                ).exclude(pk=form.instance.pk if form.instance else None).aggregate(
                                    total=Sum('delivered_quantity')
                                )['total'] or 0
                                
                                if (total_delivered_existing_in_db + delivered_quantity) > payment_item.expected_quantity:
                                    form.add_error('delivered_quantity',
                                        f"Total delivered quantity for {motorcycle_model} "
                                        f"({total_delivered_existing_in_db + delivered_quantity}) exceeds "
                                        f"expected quantity ({payment_item.expected_quantity})."
                                    )
                                
        return ValidatedDeliveryFormSet(data=data, instance=instance)
    

class DeliveryFilterForm(forms.Form):
    """Form for filtering delivery listings"""
    
    supplier = forms.ModelChoiceField(
        queryset=Supplier.objects.all(),
        required=False,
        empty_label="All Suppliers",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    payment = forms.ModelChoiceField(
        queryset=SupplierPayment.objects.all(),
        required=False,
        empty_label="All Payments",
        widget=forms.Select(attrs={'class': 'form-control '})
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control ', 'type': 'date'})
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    show_cancelled = forms.BooleanField(
        required=False,
        widget=forms.CheckboxInput(attrs={'class': 'form-check-input'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        payment_choices = [('', 'All Payments')] + [(p.id, p.payment_reference) for p in SupplierPayment.objects.all()]
        self.fields['payment'].choices = payment_choices


class InventoryFilterForm(forms.Form):
    """Form for filtering inventory listings"""
    
    brand = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Brand'})
    )
    model_name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Model Name'})
    )
    min_quantity = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Min Qty'})
    )
    max_quantity = forms.IntegerField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Max Qty'})
    )


class MotorcycleForm(forms.ModelForm):
    class Meta:
        model = Motorcycle
        fields = ['name', 'brand'] # Status is not edited here directly
        widgets = {
            'name': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter Motorcycle model name'}),
            'brand': forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Enter Motorcycle brand name'}),
        }

    def clean(self): # Your existing uniqueness validation
        cleaned_data = super().clean()
        name = cleaned_data.get('name')
        brand = cleaned_data.get('brand')

        if name and brand:
            query = Motorcycle.objects.filter(name__iexact=name, brand__iexact=brand)
            if self.instance and self.instance.pk:
                query = query.exclude(pk=self.instance.pk)
            if query.exists():
                # Using add_error for field-specific errors is better
                error_msg = f"A motorcycle with the brand '{brand}' and model name '{name}' already exists."
                self.add_error('name', error_msg)
                self.add_error('brand', error_msg)
                # raise forms.ValidationError(error_msg) # This would be a non-field error
        return cleaned_data


class MotorcycleFilterForm(forms.Form):
    name = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Model Name'}),
        label="Name"
    )
    brand = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'Brand'}),
        label="brand"
    )
    status = forms.ChoiceField(
        choices=[('', 'All Statuses')] + Motorcycle.STATUS_CHOICES,
        required=False,
        label="Status",
        widget=forms.Select(attrs={'class': 'form-control'})
    )


class CustomerForm(forms.ModelForm):
    class Meta:
        model = Customer
        fields = ['firstname', 'lastname', 'phone', 'address']
        widgets = {
            'firstname': forms.TextInput(attrs={'class': 'form-control'}),
            'lastname': forms.TextInput(attrs={'class': 'form-control'}),
            'phone': forms.TextInput(attrs={'class': 'form-control'}),
            'address': forms.Textarea(attrs={
                'class': 'form-control', 
                'rows': 3,
            }),
        }


class CustomerFilterForm(forms.Form):
    firstname = forms.CharField(
            required=False,
            widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'firstname'})
        )
    lastname = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'lastname'})
    )
    phone = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'phone'})
    )


class DepositForm(forms.ModelForm):
    class Meta:
        model = Deposit
        fields = ['customer', 'deposit_amount', 'deposit_date', 'deposit_type', 'transaction_note']
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-select'}),
            'deposit_amount': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter amount'}),
            'deposit_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'deposit_type': forms.Select(attrs={'class': 'form-select'}),
            'transaction_note': forms.Textarea(attrs={
                'class': 'form-control', 
                'rows': 3,
                'placeholder': 'Enter a transaction note (optional)'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Auto-populate deposit_date with current date for new instances
        if not self.instance.pk:
            self.fields['deposit_date'].initial = timezone.now().date()

        for field_name, field in self.fields.items():
            if isinstance(field.widget, (forms.TextInput, forms.NumberInput, forms.Textarea, forms.DateInput)):
                field.widget.attrs.update({'class': 'form-control'})
            elif isinstance(field.widget, forms.Select):
                field.widget.attrs.update({'class': 'form-select'})

    def clean(self):
        cleaned_data = super().clean()
        customer = cleaned_data.get('customer')
        deposit_amount = cleaned_data.get('deposit_amount')
        deposit_date = cleaned_data.get('deposit_date')

        # Rule 1: Customer must be selected
        if not customer:
            raise ValidationError("Please select a valid customer for this deposit.")

        # Rule 2: Deposit amount validation
        if deposit_amount is not None:
            if deposit_amount <= 0:
                raise ValidationError("Deposit amount must be greater than zero.")

        # Rule 3: Date validation
        if deposit_date:
            # Ensure deposit_date is a date object
            if hasattr(deposit_date, 'date'):
                deposit_date_obj = deposit_date.date()
            else:
                deposit_date_obj = deposit_date
            
            # Check if deposit date is in the future
            current_date = timezone.now().date()
            if deposit_date_obj > current_date:
                raise ValidationError("Deposit date cannot be in the future.")

        return cleaned_data

    def save(self, commit=True):
        deposit = super().save(commit=False)
        
        if commit:
            with transaction.atomic():
                # Store old amount for comparison (if editing)
                old_amount = None
                if deposit.pk:
                    try:
                        old_deposit = Deposit.objects.get(pk=deposit.pk)
                        old_amount = old_deposit.deposit_amount
                    except Deposit.DoesNotExist:
                        pass
                
                deposit.save()
                
                # If deposit amount changed, update status based on withdrawals
                if old_amount is not None and old_amount != deposit.deposit_amount:
                    deposit.update_status_based_on_withdrawals()
                
        return deposit

    
class DepositFilterForm(forms.Form):
    """Form for filtering deposit listings"""

    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        required=False,
        empty_label="All Customers",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    deposit_amount = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Deposit amount'})
    )

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'Choose Date From:'})
    )

    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date', 'placeholder': 'Choose Date To'})
    )

    deposit_type = forms.ChoiceField(
        choices=[('', 'All Types')] + Deposit.DEPOSIT_TYPE_CHOICES,
        required=False,
        label="Deposit Type", # Optional: set a user-friendly label
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    deposit_status = forms.ChoiceField(
        choices=[('', 'All Status')] + Deposit.DEPOSIT_STATUS_CHOICES,
        required=False,
        label="Deposit Status",
        widget=forms.Select(attrs={'class': 'form-control'})
    )


class WithdrawalForm(forms.ModelForm):
    class Meta:
        model = Withdrawal
        fields = ['deposit', 'withdrawal_amount', 'withdrawal_date', 'remarks']
        widgets = {
            'deposit': forms.Select(attrs={'class': 'form-select'}),
            'withdrawal_amount': forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Enter amount'}),
            'withdrawal_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'remarks': forms.Textarea(attrs={
                'class': 'form-control', 
                'rows': 3,
                'placeholder': 'Enter a remark (optional)'
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Auto-populate withdrawal_date with current date for new instances
        if not self.instance.pk:
            self.fields['withdrawal_date'].initial = timezone.now().date()
        
        # Filter deposits to show only active ones for new withdrawals
        # But allow all deposits when editing existing withdrawals
        if not self.instance.pk:
            self.fields['deposit'].queryset = Deposit.objects.filter(
                deposit_status__in=['active']
            ).select_related('customer')
        else:
            # When editing, include the current deposit even if it's completed
            current_deposit = self.instance.deposit
            available_deposits = Deposit.objects.filter(
                deposit_status__in=['active', 'completed']
            ).select_related('customer')
            
            if current_deposit and current_deposit not in available_deposits:
                # Include current deposit in queryset even if not in standard filter
                from django.db.models import Q
                self.fields['deposit'].queryset = Deposit.objects.filter(
                    Q(deposit_status__in=['active', 'completed']) | Q(pk=current_deposit.pk)
                ).select_related('customer')

    def clean(self):
        cleaned_data = super().clean()
        deposit = cleaned_data.get('deposit')
        withdrawal_amount = cleaned_data.get('withdrawal_amount')
        withdrawal_date = cleaned_data.get('withdrawal_date')

        # Rule 1: Deposit must be selected
        if not deposit:
            raise ValidationError("Please select a valid deposit for this withdrawal.")

        # Rule 2: Withdrawal amount validation
        if withdrawal_amount is not None:
            if withdrawal_amount <= 0:
                raise ValidationError("Withdrawal amount must be greater than zero.")

        # Rule 3: Date validation
        if withdrawal_date:
            # Ensure withdrawal_date is a date object
            if hasattr(withdrawal_date, 'date'):
                withdrawal_date_obj = withdrawal_date.date()
            else:
                withdrawal_date_obj = withdrawal_date
            
            # Check if withdrawal date is in the future
            current_date = timezone.now().date()
            if withdrawal_date_obj > current_date:
                raise ValidationError("Withdrawal date cannot be in the future.")

        # Rule 4: Enhanced balance validation with deposit-specific checking
        if deposit and withdrawal_amount:
            # Check total withdrawn from this specific deposit
            total_withdrawn_from_deposit = deposit.get_total_withdrawn()
            
            # If editing, subtract the original withdrawal amount
            if self.instance.pk:
                try:
                    original_withdrawal = Withdrawal.objects.get(pk=self.instance.pk)
                    if original_withdrawal.withdrawal_status == 'completed':
                        total_withdrawn_from_deposit -= original_withdrawal.withdrawal_amount
                except Withdrawal.DoesNotExist:
                    pass
            
            # Check if withdrawal would exceed deposit amount
            total_after_withdrawal = total_withdrawn_from_deposit + withdrawal_amount
            remaining_in_deposit = deposit.deposit_amount - total_withdrawn_from_deposit
            
            if withdrawal_amount > remaining_in_deposit:
                raise ValidationError(
                    f"Withdrawal amount exceeds remaining balance in this deposit. "
                    f"Available in deposit {deposit.deposit_reference}: ₦{remaining_in_deposit:.2f}"
                )
            
            # Also check overall customer balance (existing logic)
            customer = deposit.customer
            total_deposit = Deposit.objects.filter(
                customer=customer,
                deposit_status__in=['active', 'completed']
            ).aggregate(total_amount=Sum('deposit_amount'))['total_amount'] or 0
            
            total_withdrawn = Withdrawal.objects.filter(
                deposit__customer=customer,
                withdrawal_status='completed'
            ).aggregate(total_amount=Sum('withdrawal_amount'))['total_amount'] or 0

            # If editing, add back the original withdrawal amount
            if self.instance.pk:
                try:
                    original_withdrawal = Withdrawal.objects.get(pk=self.instance.pk)
                    if original_withdrawal.withdrawal_status == 'completed':
                        total_withdrawn -= original_withdrawal.withdrawal_amount
                except Withdrawal.DoesNotExist:
                    pass

            available_balance = total_deposit - total_withdrawn

            # Check if there is any deposit at all
            if total_deposit == 0:
                raise ValidationError("This customer has no deposit balance.")

            # Check if the withdrawal amount exceeds the available balance
            if withdrawal_amount > available_balance:
                raise ValidationError(
                    f"Insufficient overall balance. Available balance: ₦{available_balance:.2f}"
                )

        return cleaned_data

    def save(self, commit=True):
        withdrawal = super().save(commit=False)
        
        if commit:
            with transaction.atomic():
                withdrawal.save()
                # The model's save method will automatically trigger deposit status update
                
        return withdrawal


class WithdrawalFilterForm(forms.Form):
    """Form for filtering withdrawal listings"""

    deposit = forms.ModelChoiceField(
        queryset=Deposit.objects.all(),
        required=False,
        empty_label="All deposits",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    withdrawal_amount = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Withdrawal Amount'})
    )

    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )

    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )

    withdrawal_status = forms.ChoiceField(
        choices=[('', 'All Status')] + Withdrawal.WITHDRAWAL_STATUS_CHOICES,
        required=False,
        label="Withdrawal Status",
        widget=forms.Select(attrs={'class': 'form-control'})
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        deposit_choices = [('', 'All Deposits')] + [(d.id, d.deposit_reference) for d in Deposit.objects.all()]
        self.fields['deposit'].choices = deposit_choices


class LoanFilterForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        empty_label="Customer"
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control '})
    )
    status = forms.ChoiceField(
        choices=[('', 'All Statuses')] + Loan.LOAN_STATUS_CHOICES,
        required=False,
        widget=forms.Select(attrs={'class': 'form-select'}),
        label="Loan Status"
    )
    min_amount = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control ', 'placeholder': 'Min Amount'}),
        label="Min. Loan Amount"
    )
    max_amount = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Max Amount'}),
        label="Max. Loan Amount"
    )

class LoanRepaymentFilterForm(forms.Form):
    loan = forms.ModelChoiceField(
        queryset=Loan.objects.all().order_by('-loan_date'), 
        required=False,
        widget=forms.Select(attrs={'class': 'form-select select2'}),
        empty_label="Loan"
    )
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        required=False,
        widget=forms.Select(attrs={'class': 'form-select select2'}),
        empty_label="Customer"
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'type': 'date', 'class': 'form-control'})
    )
    amount = forms.DecimalField(
        required=False,
        widget=forms.NumberInput(attrs={'class': 'form-control', 'placeholder': 'Amount'}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        loan_choices = [('', 'All Loans')] + [(l.id, l.loan_reference) for l in Loan.objects.all()]
        self.fields['loan'].choices = loan_choices


class LoanForm(forms.ModelForm): #
    class Meta:
        model = Loan
        fields = ['customer', 'loan_amount', 'remarks'] # loan_date is auto, status/balance are auto
        # ... widgets from your forms.py ...
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-select select2'}),
            'loan_amount': forms.NumberInput(attrs={'placeholder': 'Enter loan amount', 'class': 'form-control', 'step': '0.01'}),
            'remarks': forms.TextInput(attrs={'placeholder': 'Remarks (e.g., for bike purchase)', 'class': 'form-control'})
        }
    # ... __init__ from your forms.py ...
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # If you want to apply select2 to all or style all uniformly
        for field_name, field in self.fields.items():
            if isinstance(field.widget, forms.Select):
                field.widget.attrs.update({'class': 'form-select select2'})
            else:
                field.widget.attrs.update({'class': 'form-control'})

    def clean_loan_amount(self): # Add validation
        amount = self.cleaned_data.get('loan_amount')
        if amount is not None and amount <= Decimal('0.00'):
            raise forms.ValidationError("Loan amount must be greater than zero.")
        return amount


# Existing LoanRepaymentForm (from your forms.py) - needs careful handling in views
class LoanRepaymentForm(forms.ModelForm): #
    class Meta:
        model = LoanRepayment
        fields = ['loan', 'repayment_amount', 'repayment_date', 'remarks']
        # ... widgets from your forms.py ...
        widgets = {
            'loan': forms.Select(attrs={'class': 'form-select select2'}),
            'repayment_amount': forms.NumberInput(attrs={'placeholder': 'Enter repayment amount', 'class': 'form-control', 'step': '0.01'}),
            'repayment_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control'}),
            'remarks': forms.TextInput(attrs={'placeholder': 'Remarks (e.g., partial payment)', 'class': 'form-control'})
        }
    # ... __init__ and clean methods from your forms.py ...
    def __init__(self, *args, **kwargs): # From your forms.py
        super().__init__(*args, **kwargs)
        self.fields['loan'].queryset = Loan.objects.filter(
            loan_status__in=['pending', 'partially repaid']
        ).select_related('customer').order_by('-loan_date', 'customer__firstname') # Added customer select_related
        if not self.instance.pk:
            self.fields['repayment_date'].initial = timezone.now().date()

    def clean_repayment_amount(self): # Add validation
        amount = self.cleaned_data.get('repayment_amount')
        if amount is not None and amount <= Decimal('0.00'):
            raise forms.ValidationError("Repayment amount must be greater than zero.")
        return amount
    
    def clean(self): # Your existing clean method from forms.py, ensure it's robust
        cleaned_data = super().clean()
        loan = cleaned_data.get('loan')
        repayment_amount = cleaned_data.get('repayment_amount')
        repayment_date = cleaned_data.get('repayment_date')

        if not loan: # Should be caught by required field, but defensive
            self.add_error('loan', "Please select a valid loan.")
            return cleaned_data # Stop further validation if loan is missing

        if loan.loan_status == 'repaid':
            self.add_error('loan', f"Loan {loan.loan_reference} is already fully repaid.")
        if loan.loan_status == 'cancelled':
            self.add_error('loan', f"Loan {loan.loan_reference} has been cancelled.")

        # Calculate effective balance for validation (considering existing repayment if editing)
        balance_to_validate_against = loan.balance
        if self.instance and self.instance.pk: # If editing
            # Add back this repayment's original amount to the loan's current balance
            # to see what the balance was *before* this repayment was applied or last updated.
            balance_to_validate_against += self.instance.repayment_amount 
        
        if repayment_amount and balance_to_validate_against is not None:
            if repayment_amount > balance_to_validate_against:
                self.add_error('repayment_amount', f"Repayment (₦{repayment_amount:,.2f}) exceeds remaining loan balance (₦{balance_to_validate_against:,.2f}).")
        
        if repayment_date:
            # Extract the date part from the repayment_datetime_obj for comparisons
            repayment_date_part = repayment_date.date()

            # Validation 1: Repayment date cannot be in the future
            current_date = timezone.now().date()
            if repayment_date_part > current_date:
                self.add_error('repayment_date', "Repayment date cannot be in the future.")
            
            # Validation 2: Repayment date cannot be before the loan date
            if loan and loan.loan_date: # loan.loan_date is also a datetime.datetime
                loan_date_part = loan.loan_date.date() # Extract its date part
                if repayment_date_part < loan_date_part:
                    self.add_error('repayment_date', "Repayment date cannot be before the loan date.")

    def save(self, commit=True): # Based on your LoanRepaymentForm.save
        repayment = super().save(commit=False) # Get instance without saving to DB yet

        if not commit: # If commit is False, just return the instance
            return repayment

        # Determine if it's an edit and get original values if so
        is_editing = bool(self.instance.pk)
        original_loan_of_repayment = None
        original_repayment_amount = Decimal('0.00')

        if is_editing:
            try:
                # Fetch the original state from DB, not self.instance which might have new form values
                original_repayment_db = LoanRepayment.objects.get(pk=self.instance.pk)
                original_loan_of_repayment = original_repayment_db.loan
                original_repayment_amount = original_repayment_db.repayment_amount
            except LoanRepayment.DoesNotExist:
                is_editing = False # Treat as new if original not found (should not happen)
        
        # Actual save of the repayment instance itself
        repayment.save() # Save the repayment first

        # --- Now handle loan balance updates ---
        current_loan_of_repayment = repayment.loan # Loan linked to the (possibly updated) repayment

        if is_editing:
            if original_loan_of_repayment == current_loan_of_repayment:
                current_loan_of_repayment.balance += original_repayment_amount # Add back original
                current_loan_of_repayment.update_balance(repayment.repayment_amount) # Subtract new, updates status & saves loan
            else:
                # Repayment edited AND loan was changed (pointed to a different loan)
                # 1. Revert effect on OLD loan
                if original_loan_of_repayment:
                    original_loan_of_repayment.update_balance(-original_repayment_amount) # Add back amount to old loan
                
                # 2. Apply effect on NEW loan
                current_loan_of_repayment.update_balance(repayment.repayment_amount) # Subtract amount from new loan
        else: # New repayment
            current_loan_of_repayment.update_balance(repayment.repayment_amount) # Subtract from loan

        return repayment
    

class SaleCreateForm(forms.ModelForm):
    class Meta:
        model = Sale
        fields = [
            'customer', 'motorcycle', 'sale_date', 
            'payment_type', 'final_price',
            'engine_no', 'chassis_no', 'remarks'
        ]
        widgets = {
            'customer': forms.Select(attrs={'class': 'form-select select2', 'placeholder':'Customer'}),
            'motorcycle': forms.Select(attrs={'class': 'form-select select2'}),
            'sale_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'payment_type': forms.Select(attrs={'class': 'form-select'}),
            'final_price': forms.NumberInput(attrs={'class': 'form-control', 'step': '0.01'}),
            'engine_no': forms.TextInput(attrs={'class': 'form-control'}),
            'chassis_no': forms.TextInput(attrs={'class': 'form-control'}),
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['sale_date'].initial = timezone.now().date()

    def clean_sale_date(self):
            sale_datetime_obj = self.cleaned_data.get('sale_date') # This is a datetime.datetime object
            
            if sale_datetime_obj:
                # Extract the date part from the datetime object for comparison
                sale_date_part = sale_datetime_obj.date() 
                
                current_date = timezone.now().date()
                if sale_date_part > current_date:
                    raise forms.ValidationError("Sale date cannot be in the future.")
            
            return sale_datetime_obj # Return the original datetime object, as the model field is a DateTimeField

    def clean_final_price(self):
        final_price = self.cleaned_data.get('final_price')
        if final_price is not None and final_price <= Decimal('0.00'):
            raise forms.ValidationError("Final price must be greater than zero.")
        return final_price
        
    def clean_engine_no(self):
        engine_no = self.cleaned_data.get('engine_no')
        if Sale.objects.filter(engine_no__iexact=engine_no).exists():
            raise forms.ValidationError("This Engine Number has already been recorded in a sale.")
        return engine_no

    def clean_chassis_no(self):
        chassis_no = self.cleaned_data.get('chassis_no')
        if Sale.objects.filter(chassis_no__iexact=chassis_no).exists():
            raise forms.ValidationError("This Chassis Number has already been recorded in a sale.")
        return chassis_no

    def clean(self):
        cleaned_data = super().clean()
        motorcycle = cleaned_data.get('motorcycle')
        payment_type = cleaned_data.get('payment_type')
        final_price = cleaned_data.get('final_price')
        customer = cleaned_data.get('customer')

        # 1. Inventory Check [cite: 4] (adapted from general validation request)
        if motorcycle:
            try:
                inventory = Inventory.objects.get(motorcycle_model=motorcycle)
                if inventory.current_quantity <= 0:
                    self.add_error('motorcycle', f"'{motorcycle}' is currently out of stock.")
            except Inventory.DoesNotExist:
                self.add_error('motorcycle', f"Inventory record for '{motorcycle}' not found. Cannot sell.")
        
        # 2. Deposit Balance Check for Deposit Payments [cite: 1, 17]
        if payment_type == 'DEPOSIT' and customer and final_price is not None:

            available_balance = Withdrawal.get_customer_balance(customer)
            
            if available_balance < final_price:
                self.add_error('final_price', 
                               f"Customer's available deposit balance (₦{available_balance:,.2f}) is insufficient "
                               f"to cover the final price (₦{final_price:,.2f}).")
            
        return cleaned_data


class SaleEditForm(forms.ModelForm):
    class Meta:
        model = Sale
        # Editable fields only [cite: 1]
        fields = ['sale_date', 'engine_no', 'chassis_no', 'remarks']
        widgets = {
            'sale_date': forms.DateInput(attrs={'class': 'form-control', 'type': 'date'}),
            'engine_no': forms.TextInput(attrs={'class': 'form-control'}),
            'chassis_no': forms.TextInput(attrs={'class': 'form-control'}),
            'remarks': forms.Textarea(attrs={'class': 'form-control', 'rows': 3}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        instance = getattr(self, 'instance', None)
        if instance and instance.pk:
            pass
    
    # def clean_sale_date(self):
    #     sale_date = self.cleaned_data.get('sale_date')
    #     if sale_date and sale_date > timezone.now().date():
    #         raise forms.ValidationError("Sale date cannot be in the future.")
    #     # Add more specific date validation if needed (e.g., not before original creation date + reasonable window)
    #     return sale_date

    def clean_sale_date(self):
        sale_datetime_obj = self.cleaned_data.get('sale_date') # This is a datetime.datetime object
        
        if sale_datetime_obj:
            # Extract the date part from the datetime object for comparison
            sale_date_part = sale_datetime_obj.date() 
            
            current_date = timezone.now().date()
            if sale_date_part > current_date:
                raise forms.ValidationError("Sale date cannot be in the future.")
        
        return sale_datetime_obj # Return the original datetime object, as the model field is a DateTimeField

    def clean_engine_no(self):
        engine_no = self.cleaned_data.get('engine_no')
        # Ensure uniqueness if changed
        if self.instance and self.instance.pk and engine_no != self.instance.engine_no:
            if Sale.objects.filter(engine_no__iexact=engine_no).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError("This Engine Number is already in use by another sale.")
        return engine_no

    def clean_chassis_no(self):
        chassis_no = self.cleaned_data.get('chassis_no')
        # Ensure uniqueness if changed
        if self.instance and self.instance.pk and chassis_no != self.instance.chassis_no:
            if Sale.objects.filter(chassis_no__iexact=chassis_no).exclude(pk=self.instance.pk).exists():
                raise forms.ValidationError("This Chassis Number is already in use by another sale.")
        return chassis_no
    

class SaleFilterForm(forms.Form):
    customer = forms.ModelChoiceField(
        queryset=Customer.objects.all(),
        required=False,
        empty_label="Customer",
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    motorcycle = forms.ModelChoiceField(
        queryset=Motorcycle.objects.all(),
        required=False,
        empty_label="Motorcycle",
        widget=forms.Select(attrs={'class': 'form-select'}),
    )
    engine_no = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'engine_no'}),
        label="Engine_No"
    )
    chasis_no = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={'class': 'form-control', 'placeholder': 'chasis_no'}),
        label="Chasis_No"
    )
    date_from = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    date_to = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs={'class': 'form-control', 'type': 'date'})
    )
    payment_type = forms.ChoiceField(
        choices=[('', 'All types')] + Sale.PAYMENT_TYPE_CHOICES,
        required=False,
        label="Payment Types",
        widget=forms.Select(attrs={'class': 'form-control'})
    )
    status = forms.ChoiceField(
        choices=[('', 'All Statuses')] + Sale.STATUS_CHOICES,
        required=False,
        label="Sale Status",
        widget=forms.Select(attrs={'class': 'form-control'})
    )


class ActivityLogFilterForm(forms.Form):
    PERIOD_CHOICES = [
        ('today', 'Today'),
        ('yesterday', 'Yesterday'),
        ('this_week', 'This Week'),
        ('last_7_days', 'Last 7 Days'),
        ('this_month', 'This Month'),
    ]

    VIEW_CHOICES = [
        ('summary', 'Summary'),
        ('detailed', 'Detailed View'),
    ]
    
    period = forms.ChoiceField(
        choices=PERIOD_CHOICES,
        required=True,
        label="Select Period",
        widget=forms.Select(attrs={'class': 'form-select'})
    )

    view_type = forms.ChoiceField(
        choices=VIEW_CHOICES,
        required=True,
        label="View Type",
        initial='summary',
        widget=forms.Select(attrs={'class': 'form-select'})
    )