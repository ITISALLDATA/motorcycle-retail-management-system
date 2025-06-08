from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.utils.timezone import localtime
from .forms import *
from .models import *
from django.db.models import F, Sum, Q, Count, DecimalField, Avg, OuterRef, Subquery, Value, IntegerField
from django.urls import reverse
from django.db import transaction, IntegrityError 
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.generic import ListView, DetailView, TemplateView
from django.views.decorators.http import require_http_methods
from decimal import Decimal
from django.db.models.functions import Coalesce
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
import datetime
from django.utils.safestring import mark_safe
import json
from operator import itemgetter


LOW_STOCK_THRESHOLD = 2


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        now = timezone.now()
        today = now.date()
        
        start_of_this_week = today - timedelta(days=today.weekday())
        start_of_this_month = today.replace(day=1)
        start_of_this_year = today.replace(month=1, day=1)

        context['today_date'] = today
        # --- Section 1: Sales & Customer Activity ---
        active_sales_this_week_qs = Sale.objects.filter(sale_date__date__gte=start_of_this_week, status='ACTIVE')
        context['sales_this_week_count'] = active_sales_this_week_qs.count()
        context['sales_this_week_value'] = active_sales_this_week_qs.aggregate(total=Coalesce(Sum('final_price'), Value(Decimal('0.00'))))['total']
        
        active_sales_this_month_qs = Sale.objects.filter(sale_date__date__gte=start_of_this_month, status='ACTIVE')
        context['sales_this_month_count'] = active_sales_this_month_qs.count()
        context['sales_this_month_value'] = active_sales_this_month_qs.aggregate(total=Coalesce(Sum('final_price'), Value(Decimal('0.00'))))['total']

        active_sales_this_year_qs = Sale.objects.filter(sale_date__date__gte=start_of_this_year, status='ACTIVE')
        context['sales_this_year_count'] = active_sales_this_year_qs.count()
        context['sales_this_year_value'] = active_sales_this_year_qs.aggregate(total=Coalesce(Sum('final_price'), Value(Decimal('0.00'))))['total']

        context['recent_sales'] = Sale.objects.filter(status='ACTIVE').select_related('customer', 'motorcycle').order_by('-sale_date', '-created_at')[:6]
        
        context['top_selling_models_year_qty'] = Sale.objects.filter(
            sale_date__date__gte=start_of_this_year, status='ACTIVE'
        ).values('motorcycle__brand', 'motorcycle__name').annotate(count=Count('motorcycle')).order_by('-count')[:3]

        queryset = Sale.objects.filter(sale_date__date__gte=start_of_this_month,status='ACTIVE').values('payment_type').annotate(count=Count('id'),total_value=Sum('final_price')).order_by('-total_value')

        context['sales_by_payment_type_month'] = list(queryset)

        # Customer Financial Snapshot
        active_deposits_with_balance = Deposit.objects.filter(deposit_status='active').annotate(
            current_withdrawals_sum=Coalesce(Sum('withdrawal__withdrawal_amount', filter=Q(withdrawal__withdrawal_status='completed')), Value(Decimal('0.00')), output_field=DecimalField()),
            calculated_remaining_balance=F('deposit_amount') - F('current_withdrawals_sum')
        )
        total_customer_deposit_balance_agg = active_deposits_with_balance.aggregate(
            total_balance=Coalesce(Sum('calculated_remaining_balance'), Value(Decimal('0.00')), output_field=DecimalField())
        )
        context['total_customer_deposit_balance'] = total_customer_deposit_balance_agg['total_balance']

        context['recent_deposits'] = Deposit.objects.select_related('customer').order_by('-deposit_date')[:3]
        context['recent_withdrawals'] = Withdrawal.objects.select_related('deposit__customer', 'sale').order_by('-withdrawal_date')[:3]


        # --- Section 2: Loans & Supplier Payments ---
        context['total_outstanding_loan_balance'] = Loan.objects.filter(loan_status__in=['pending', 'partially repaid']).aggregate(total=Coalesce(Sum('balance'), Value(Decimal('0.00'))))['total']
        context['recent_loans'] = Loan.objects.select_related('customer', 'sale').order_by('-loan_date')[:3]
        context['recent_repayments'] = LoanRepayment.objects.select_related('loan__customer').order_by('-repayment_date')[:5]

        # Assuming SupplierPayment.ACTIVE and SupplierPayment.COMPLETED are defined as constants on the SupplierPayment model
        context['recent_supplier_payments'] = SupplierPayment.objects.select_related('supplier').order_by('-payment_date')[:5]


        # --- Section 3: Inventory & Operations ---
        context['total_inventory_units'] = Inventory.objects.aggregate(total=Coalesce(Sum('current_quantity'), Value(0)))['total']
        
        latest_price_subquery = SupplierPaymentItem.objects.filter(
            motorcycle_model_id=OuterRef('motorcycle_model_id')
        ).order_by(
            '-payment__payment_date', '-payment__created_at', '-id'
        ).values('unit_price') # Select only the unit_price
        latest_price_subquery.output_field = DecimalField()

        # 2. Annotate current inventory items (with quantity > 0) with this latest cost
        inventory_items_with_latest_cost = Inventory.objects.filter(current_quantity__gt=0).annotate(
            latest_unit_cost=Coalesce(
                Subquery(latest_price_subquery[:1]), # Takes the first result (latest price)
                Value(Decimal('0.00')),
                output_field=DecimalField()
            )
        )

        # 3. Calculate total estimated value by summing (current_quantity * latest_unit_cost) for all items
        total_inventory_value_data = inventory_items_with_latest_cost.aggregate(
            total_value=Sum(
                F('current_quantity') * F('latest_unit_cost'),
                output_field=DecimalField()
            )
        )
        context['estimated_total_inventory_value'] = total_inventory_value_data['total_value'] or Decimal('0.00')

        delivered_qty_subquery = SupplierDeliveryItem.objects.filter(
            delivery__payment_id=OuterRef('payment_id'),         # Links to the SupplierPaymentItem's parent payment
            motorcycle_model_id=OuterRef('motorcycle_model_id'),# Links to the SupplierPaymentItem's motorcycle_model
            delivery__is_cancelled=False                         # Only consider non-cancelled deliveries
        ).values(
            'delivery__payment_id', 'motorcycle_model_id' # Group by these for the sum
        ).annotate(
            total_delivered_for_item_on_payment=Sum('delivered_quantity')
        ).values('total_delivered_for_item_on_payment')
        delivered_qty_subquery.output_field = IntegerField()

        active_payment_items_with_delivery_info = SupplierPaymentItem.objects.filter(
            payment__status=SupplierPayment.ACTIVE 
        ).annotate(
            total_delivered=Coalesce(
                Subquery(delivered_qty_subquery[:1]), # Apply the subquery, taking the single summed value
                Value(0),                             # Default to 0 if no deliveries found for this item
                output_field=IntegerField()
            )
        )

        undelivered_summary = active_payment_items_with_delivery_info.annotate(
            undelivered_qty_per_item=F('expected_quantity') - F('total_delivered')
        ).filter(
            undelivered_qty_per_item__gt=0 # Only include items where there's actually something undelivered
        ).aggregate(
            grand_total_undelivered_units=Coalesce(
                Sum('undelivered_qty_per_item'), 
                Value(0), 
                output_field=IntegerField()
            ),
            grand_total_undelivered_value=Coalesce(
                Sum(F('undelivered_qty_per_item') * F('unit_price'), output_field=DecimalField()), 
                Value(Decimal('0.00'))
            )
        )

        context['total_undelivered_units_from_suppliers'] = undelivered_summary['grand_total_undelivered_units']
        context['total_undelivered_value_from_suppliers'] = undelivered_summary['grand_total_undelivered_value']
       

        context['low_stock_items_count'] = Inventory.objects.filter(current_quantity__gt=0, current_quantity__lte=LOW_STOCK_THRESHOLD).count()
        context['out_of_stock_items_count'] = Inventory.objects.filter(current_quantity__lte=0).count()
        
        context['recent_inventory_transactions'] = InventoryTransaction.objects.select_related('motorcycle_model').order_by('-transaction_date')[:5]
        
        context['recent_supplier_deliveries'] = SupplierDelivery.objects.select_related('payment__supplier').order_by('-delivery_date')[:3]
        
        context['title'] = "Dashboard Overview"
        return context
    

class CustomerListView(LoginRequiredMixin, ListView):
    """List view for customers with deposit and withdrawal summaries."""
    model = Customer
    template_name = 'customer_list.html'
    context_object_name = 'customers'
    paginate_by = 20

    def get_queryset(self):
        queryset = Customer.objects.all()

        self.filter_form = CustomerFilterForm(self.request.GET or None)

        if self.filter_form.is_valid():
            cleaned_data = self.filter_form.cleaned_data
            
            if cleaned_data.get('firstname'):
                queryset = queryset.filter(firstname__icontains=cleaned_data['firstname'])
            if cleaned_data.get('lastname'):
                queryset = queryset.filter(lastname__icontains=cleaned_data['lastname'])
            if cleaned_data.get('phone'):
                queryset = queryset.filter(phone__icontains=cleaned_data['phone'])

        # Annotate with total deposits and withdrawals for each customer
        annotated_queryset = queryset.annotate(
            total_deposits_raw=Sum(
                'deposit__deposit_amount',
                filter=Q(deposit__deposit_status__in=['active', 'completed']),
                # Ensure the output field is DecimalField
                output_field=DecimalField()
            ),
            total_withdrawals_raw=Sum(
                'deposit__withdrawal__withdrawal_amount',
                filter=Q(deposit__withdrawal__withdrawal_status='completed'),
                # Ensure the output field is DecimalField
                output_field=DecimalField()
            )
        ).annotate(
            total_deposits=Coalesce('total_deposits_raw', Decimal('0.00'), output_field=DecimalField()),
            total_withdrawals=Coalesce('total_withdrawals_raw', Decimal('0.00'), output_field=DecimalField())
        ).annotate(
            current_balance=F('total_deposits') - F('total_withdrawals')
        )

        return annotated_queryset.order_by('lastname', 'firstname')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Pass the filter form instance to the template
        context['filter_form'] = self.filter_form
        return context


@login_required
def customer_create(request):
    """Create new customer"""
    if request.method == 'POST':
        form = CustomerForm(request.POST)
        if form.is_valid():
            customer = form.save()
            messages.success(request, f'Customer "{customer.name}" created successfully.')
            return redirect('customer_detail', pk=customer.pk)
    else:
        form = CustomerForm()
    
    return render(request, 'customer_form.html', {
        'form': form,
        'title': 'Create Customer'
    })


class CustomerDetailView(LoginRequiredMixin, DetailView):
    model = Customer
    template_name = 'customer_detail.html'
    context_object_name = 'customer'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        customer = self.object
        deposits = customer.deposit_set.filter(
            deposit_status__in=['active', 'completed']
        ).order_by('-deposit_date')

        withdrawals = Withdrawal.objects.filter(
            deposit__customer=customer,
            withdrawal_status='completed'
        ).order_by('-withdrawal_date')
        
        # Calculate summary statistics
        stats = {
            'total_deposits_count': deposits.count(),
            'total_deposits_amount': deposits.aggregate(Sum('deposit_amount'))['deposit_amount__sum'] or Decimal('0.00'),
            'total_withdrawals_count': withdrawals.count(),
            'total_withdrawals_amount': withdrawals.aggregate(Sum('withdrawal_amount'))['withdrawal_amount__sum'] or Decimal('0.00'),
        }

        # Calculate current balance
        stats['current_balance'] = stats['total_deposits_amount'] - stats['total_withdrawals_amount']
        
        # Add the extra context
        context['deposits'] = deposits[:10]    # Show recent 10 deposits
        context['withdrawals'] = withdrawals[:10] # Show recent 10 withdrawals
        context['stats'] = stats
        
        return context


@login_required
def customer_edit(request, pk):
    """Edit customer details."""
    customer = get_object_or_404(Customer, pk=pk)
    
    if request.method == 'POST':
        form = CustomerForm(request.POST, instance=customer)
        if form.is_valid():
            form.save()
            messages.success(request, f'Customer "{customer.name}" updated successfully.')
            return redirect('customer_detail', pk=customer.pk) # Redirect to the customer's detail page
        else:
            # If form is not valid, print errors (for debugging) and re-render the form
            print(form.errors) 
    else:
        form = CustomerForm(instance=customer)
    
    return render(request, 'customer_form.html', {
        'form': form,
        'customer': customer, # Pass the customer instance to the template
        'title': 'Edit Customer',
        'edit_mode': True, # Indicate that this is an edit operation
    })


class SupplierListView(LoginRequiredMixin, ListView):
    model = Supplier
    template_name = 'supplier_list.html'
    context_object_name = 'suppliers'
    paginate_by = 20

    def get_queryset(self):
        queryset = Supplier.objects.all() # Start with the base queryset

        self.filter_form = SupplierFilterForm(self.request.GET or None)

        if self.filter_form.is_valid():
            cleaned_data = self.filter_form.cleaned_data

            if cleaned_data.get('name'):
                queryset = queryset.filter(name__icontains=cleaned_data['name'])
            if cleaned_data.get('phone'):
                queryset = queryset.filter(phone__icontains=cleaned_data['phone'])


        annotated_queryset = queryset.annotate(
            total_payments=Count('payments', filter=Q(payments__status=SupplierPayment.ACTIVE)),
            total_amount=Sum('payments__amount_paid', filter=Q(payments__status=SupplierPayment.ACTIVE))
        )


        return annotated_queryset.order_by('name')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not hasattr(self, 'filter_form'): # Ensure filter_form is available
            self.filter_form = SupplierFilterForm(self.request.GET or None)
        context['filter_form'] = self.filter_form
        return context


@login_required
def supplier_create(request):
    """Create new supplier"""
    if request.method == 'POST':
        form = SupplierForm(request.POST)
        if form.is_valid():
            supplier = form.save()
            messages.success(request, f'Supplier "{supplier.name}" created successfully.')
            return redirect('supplier_detail', pk=supplier.pk)
    else:
        form = SupplierForm()
    
    return render(request, 'supplier_form.html', {
        'form': form,
        'title': 'Create Supplier'
    })


class SupplierDetailView(LoginRequiredMixin, DetailView):
    model = Supplier
    template_name = 'supplier_detail.html'
    context_object_name = 'supplier'

    def get_context_data(self, **kwargs):
        # Call the base implementation first to get a context
        context = super().get_context_data(**kwargs)
        
        # Get the supplier object, available as self.object
        supplier = self.object

        # Create subquery for delivered quantities
        delivered_qty_subquery = SupplierDeliveryItem.objects.filter(
            delivery__payment_id=OuterRef('payment_id'),         # Links to the SupplierPaymentItem's parent payment
            motorcycle_model_id=OuterRef('motorcycle_model_id'), # Links to the SupplierPaymentItem's motorcycle_model
            delivery__is_cancelled=False                         # Only consider non-cancelled deliveries
        ).values(
            'delivery__payment_id', 'motorcycle_model_id'       # Group by these for the sum
        ).annotate(
            total_delivered_for_item_on_payment=Sum('delivered_quantity')
        ).values('total_delivered_for_item_on_payment')
        delivered_qty_subquery.output_field = IntegerField()

        # Filter payment items to only this specific supplier
        active_payment_items_with_delivery_info = SupplierPaymentItem.objects.filter(
            payment__supplier=supplier,  # THIS IS THE KEY CHANGE - filter by this specific supplier
            payment__status=SupplierPayment.ACTIVE 
        ).annotate(
            total_delivered=Coalesce(
                Subquery(delivered_qty_subquery[:1]), # Apply the subquery, taking the single summed value
                Value(0),                             # Default to 0 if no deliveries found for this item
                output_field=IntegerField()
            )
        )

        # Calculate undelivered summary for this supplier only
        undelivered_summary = active_payment_items_with_delivery_info.annotate(
            undelivered_qty_per_item=F('expected_quantity') - F('total_delivered')
        ).filter(
            undelivered_qty_per_item__gt=0 # Only include items where there's actually something undelivered
        ).aggregate(
            grand_total_undelivered_units=Coalesce(
                Sum('undelivered_qty_per_item'), 
                Value(0), 
                output_field=IntegerField()
            ),
            grand_total_undelivered_value=Coalesce(
                Sum(F('undelivered_qty_per_item') * F('unit_price'), output_field=DecimalField()), 
                Value(Decimal('0.00'))
            )
        )

        # Get payments for this supplier only
        payments = supplier.payments.exclude(status=SupplierPayment.CANCELLED).order_by('-payment_date')
        
        # Calculate summary statistics
        stats = {
            'total_undelivered_units_from_suppliers': undelivered_summary['grand_total_undelivered_units'],
            'total_undelivered_value_from_suppliers': undelivered_summary['grand_total_undelivered_value']
        }
        
        # Add the extra context
        context['payments'] = payments[:10]  # Show recent 10 payments
        context['stats'] = stats
        
        return context
    

@login_required
def supplier_edit(request, pk):
    """Edit supplier"""
    supplier = get_object_or_404(Supplier, pk=pk)
    
    if request.method == 'POST':
        form = SupplierForm(request.POST, instance=supplier)
        if form.is_valid():
            form.save()
            messages.success(request, f'Supplier "{supplier.name}" updated successfully.')
            return redirect('supplier_detail', pk=supplier.pk)
    else:
        form = SupplierForm(instance=supplier)
    
    return render(request, 'supplier_form.html', {
        'form': form,
        'supplier': supplier,
        'title': 'Edit Supplier'
    })


# Payment Views
class PaymentListView(LoginRequiredMixin, ListView):
    model = SupplierPayment
    template_name = 'payment_list.html' # You provided this
    context_object_name = 'payments'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = SupplierPayment.objects.select_related('supplier').prefetch_related(
            'payment_items__motorcycle_model',
            'deliveries'
        )
        
        form = PaymentFilterForm(self.request.GET)
        if form.is_valid():
            if form.cleaned_data['supplier']:
                queryset = queryset.filter(supplier=form.cleaned_data['supplier'])
            if form.cleaned_data['payment_method']:
                queryset = queryset.filter(payment_method=form.cleaned_data['payment_method'])
            if form.cleaned_data['date_from']:
                queryset = queryset.filter(payment_date__gte=form.cleaned_data['date_from'])
            if form.cleaned_data['date_to']:
                queryset = queryset.filter(payment_date__lte=form.cleaned_data['date_to'])
            
            # Filter by new status field
            status_filter = form.cleaned_data.get('status')
            if status_filter:
                queryset = queryset.filter(status=status_filter)
            # By default, if no status filter, show all non-cancelled or define a default (e.g. ACTIVE)
            # else:
            #    queryset = queryset.exclude(status=SupplierPayment.CANCELLED) # Example: hide cancelled by default

        return queryset.order_by('-payment_date', '-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = PaymentFilterForm(self.request.GET)
        return context


@login_required
@transaction.atomic
def payment_create(request):
    formset_helper = SupplierPaymentItemFormSetHelper()
    parent_amount_paid = None

    if request.method == 'POST':
        form = SupplierPaymentForm(request.POST)
        
        # Tentatively get amount_paid from POST data for the formset/forms if parent form is valid up to that field
        # This is for the item-level validation (Clarified Point 3)
        try:
            parent_amount_paid = Decimal(request.POST.get('amount_paid', '0'))
            if parent_amount_paid < Decimal('200000.00'): # Apply min amount check early if possible
                # This will be caught by form.is_valid() anyway, but useful for formset if it runs first
                pass
        except (InvalidOperation, TypeError):
            parent_amount_paid = None # Let the main form validation catch this error

        # Create an unsaved instance for the formset.
        # The main form's .is_valid() will later clean and populate its .instance properly.
        payment_instance_for_formset = form.instance # Unsaved
        if parent_amount_paid is not None: # If we got a decimal, set it on the unsaved instance
             payment_instance_for_formset.amount_paid = parent_amount_paid


        formset = formset_helper.get_formset(
            data=request.POST, 
            instance=payment_instance_for_formset,
            parent_amount_paid_for_items=parent_amount_paid # Pass to formset and forms
        )

        if form.is_valid() and formset.is_valid():
            payment = form.save(commit=False)
            payment.status = SupplierPayment.ACTIVE
            payment.save() # Save parent

            formset.instance = payment # Assign saved parent to formset
            formset.save() # Save items

            payment.update_completion_status()
            messages.success(request, f'Payment "{payment.payment_reference}" created successfully.')
            return redirect('payment_detail', pk=payment.pk)
        else:
            if not form.is_valid():
                messages.error(request, "Please correct errors in the payment information.")
            if not formset.is_valid():
                messages.error(request, "Please correct errors in the payment items.")
                # Detailed formset errors can be added to messages if needed
                # for key, error_list in formset.errors_json.items(): ...
    else: # GET
        form = SupplierPaymentForm()
        # For a new payment, amount_paid is not yet known for item validation,
        # so item-level validation against amount_paid will be skipped or might use None.
        formset = formset_helper.get_formset(instance=SupplierPayment(), parent_amount_paid_for_items=None)


    return render(request, 'payment_form.html', {
        'form': form,
        'formset': formset,
        'title': 'Create New Payment'
    })


class PaymentDetailView(LoginRequiredMixin, DetailView):
    model = SupplierPayment
    template_name = 'payment_detail.html'
    context_object_name = 'payment'

    def get_queryset(self):
        """
        Override get_queryset to include select_related and prefetch_related
        for optimized data retrieval.
        """
        queryset = super().get_queryset().select_related('supplier').prefetch_related(
            'payment_items__motorcycle_model',
            'deliveries__delivery_items__motorcycle_model'
        )
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        payment = self.object # The SupplierPayment instance

        # Recalculate delivery status for items
        delivery_status_list = []
        for item in payment.payment_items.all(): # payment_items are pre_fetched
            delivered_qty = SupplierDeliveryItem.objects.filter(
                delivery__payment=payment,
                motorcycle_model=item.motorcycle_model,
                delivery__is_cancelled=False
            ).aggregate(total=Sum('delivered_quantity'))['total'] or 0
            
            delivery_status_list.append({
                'item': item,
                'delivered_quantity': delivered_qty,
                'remaining_quantity': item.expected_quantity - delivered_qty,
                'total_value': item.unit_price * item.expected_quantity,
                'is_complete': delivered_qty >= item.expected_quantity
            })
        
        context['delivery_status'] = delivery_status_list
        # deliveries are pre_fetched, so this filter is efficient
        context['deliveries'] = payment.deliveries.filter(is_cancelled=False) 
        
        return context


@login_required
@transaction.atomic
def payment_edit(request, pk):
    payment = get_object_or_404(SupplierPayment, pk=pk)
    formset_helper = SupplierPaymentItemFormSetHelper()

    if not payment.is_editable:
        messages.error(request, f'Payment {payment.payment_reference} ({payment.get_status_display()}) cannot be edited.')
        return redirect('payment_detail', pk=payment.pk)

    if request.method == 'POST':
        form = SupplierPaymentForm(request.POST, instance=payment) # Step 1: Bind main form

        # Step 2: Validate the main form first to get the cleaned 'amount_paid'
        if form.is_valid():
            # `form.cleaned_data['amount_paid']` is now the validated amount_paid from this submission.
            # `form.instance.amount_paid` (which is `payment.amount_paid`) has also been updated.
            cleaned_parent_amount_paid = form.cleaned_data['amount_paid']

            # Step 3: Now initialize the formset with the cleaned data from the parent
            formset = formset_helper.get_formset(
                data=request.POST,
                instance=payment, # `payment` instance now reflects cleaned data from `form`
                parent_amount_paid_for_items=cleaned_parent_amount_paid
            )

            if formset.is_valid(): # Step 4: Validate the formset
                saved_payment = form.save() # Main form is already validated
                
                formset.instance = saved_payment # Ensure formset is linked to the saved payment
                formset.save() # Save items

                saved_payment.update_completion_status(force_recalculate=True)
                messages.success(request, f'Payment "{saved_payment.payment_reference}" updated successfully.')
                return redirect('payment_detail', pk=saved_payment.pk)
            else:
                # Formset is invalid, main form was valid.
                messages.error(request, "Please correct errors in the payment items.")
                # `form` (valid) and `formset` (invalid) will be passed to render.
        else:
            # Main form is invalid. We still need to initialize the formset for rendering,
            # possibly with the original amount_paid or a tentative one from POST for item forms.
            # The formset's primary "total == amount_paid" check will likely be less critical
            # if the main form's amount_paid itself is invalid.
            
            # Try to get a tentative amount_paid from POST for item forms, even if parent form is invalid
            tentative_amount_paid = None
            try:
                tentative_amount_paid = Decimal(request.POST.get('amount_paid', '0'))
            except (InvalidOperation, TypeError):
                tentative_amount_paid = payment.amount_paid # Fallback to original if POST data is bad

            formset = formset_helper.get_formset(
                data=request.POST, # Re-pass POST data to repopulate formset fields
                instance=payment,  # The original payment instance
                parent_amount_paid_for_items=tentative_amount_paid
            )
            messages.error(request, "Please correct errors in the payment information.")
            # `form` (invalid) and `formset` (bound with POST data) will be passed to render.

    else: # GET request
        form = SupplierPaymentForm(instance=payment)
        formset = formset_helper.get_formset(
            instance=payment,
            parent_amount_paid_for_items=payment.amount_paid # Use current amount for initial display
        )

    return render(request, 'payment_form.html', {
        'form': form,
        'formset': formset,
        'payment': payment,
        'title': f'Edit Payment {payment.payment_reference}',
        'edit_mode': True
    })


@login_required
@transaction.atomic # Keep for the POST part
def payment_cancel(request, pk):
    payment = get_object_or_404(SupplierPayment, pk=pk)

    if not payment.is_cancellable: # Using the model property
        messages.error(request, f'Payment "{payment.payment_reference}" ({payment.get_status_display()}) cannot be cancelled at this stage (e.g., it might have deliveries or is not in an active state).')
        return redirect('payment_detail', pk=payment.pk)

    if request.method == 'POST':
        payment.status = SupplierPayment.CANCELLED
        payment.remarks = (payment.remarks or "") + f"\nCancelled on {timezone.now().strftime('%Y-%m-%d %H:%M')} by {request.user.username if request.user.is_authenticated else 'system'}."
        payment.save(update_fields=['status', 'remarks', 'updated_at'])
        messages.success(request, f'Payment "{payment.payment_reference}" has been cancelled.')
        return redirect('payment_detail', pk=payment.pk) # Or payment_list

    # For GET request, prepare context for the generic confirmation template
    consequences = [
        "The payment status will be set to 'Cancelled'.",
        "This payment will no longer be considered active for new deliveries or further processing."
    ]
    if payment.has_deliveries: # This check is within is_cancellable, but good for context
        consequences.append("Note: This payment already has deliveries. Cancelling the payment does not automatically cancel its associated deliveries.")


    context = {
        'item': payment,
        'item_type': "Supplier Payment",
        'item_identifier': payment.payment_reference or f"ID {payment.pk}",
        'cancel_action_url': request.path, # The current URL will handle the POST
        'back_url': reverse('payment_detail', kwargs={'pk': payment.pk}),
        'additional_warning_message': "Cancelling this payment will mark it as void. This action cannot be easily undone.",
        'cancellation_consequences': consequences,
        'action_verb': 'Cancel Payment'
    }
    return render(request, 'generic_cancel_confirm.html', context)


# Delivery Views
class DeliveryListView(LoginRequiredMixin, ListView):
    """List view for deliveries"""
    model = SupplierDelivery
    template_name = 'delivery_list.html'
    context_object_name = 'deliveries'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = SupplierDelivery.objects.select_related(
            'payment__supplier'
        ).prefetch_related(
            'delivery_items__motorcycle_model'
        )
        
        # Apply filters
        form = DeliveryFilterForm(self.request.GET)
        if form.is_valid():
            if form.cleaned_data['supplier']:
                queryset = queryset.filter(payment__supplier=form.cleaned_data['supplier'])
            
            if form.cleaned_data['payment']:
                queryset = queryset.filter(payment=form.cleaned_data['payment'])
            
            if form.cleaned_data['date_from']:
                queryset = queryset.filter(delivery_date__gte=form.cleaned_data['date_from'])
            
            if form.cleaned_data['date_to']:
                queryset = queryset.filter(delivery_date__lte=form.cleaned_data['date_to'])
            
            if not form.cleaned_data.get('show_cancelled'):
                queryset = queryset.filter(is_cancelled=False)
        else:
            queryset = queryset.filter(is_cancelled=False)
        
        return queryset.order_by('-delivery_date', '-created_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = DeliveryFilterForm(self.request.GET)
        return context


@login_required
@transaction.atomic
def delivery_create(request):

    formset_helper = SupplierDeliveryItemFormSetHelper()
    if request.method == 'POST':
        form = SupplierDeliveryForm(request.POST)
        delivery_instance_for_formset = form.instance 
        formset = formset_helper.get_formset(request.POST, instance=delivery_instance_for_formset)

        is_form_valid = form.is_valid()
        is_formset_valid = formset.is_valid()

        if is_form_valid and is_formset_valid:
            try:
                delivery = form.save() 
                formset.instance = delivery 
                formset.save() 

                if delivery.payment: # Trigger payment status update
                    delivery.payment.update_completion_status(force_recalculate=True)

                messages.success(
                    request,
                    f'Delivery "{delivery.delivery_reference}" recorded successfully with '
                    f'{delivery.delivery_items.count()} items.'
                )
                return redirect('delivery_detail', pk=delivery.pk)
            # ... (your existing exception handling) ...
            except ValidationError as e:
                # (Handle error)
                error_message_list = e.messages if hasattr(e, 'messages') else [str(e)]
                if hasattr(e, 'error_dict'): 
                     for field, errors_list in e.error_dict.items():
                        if field == '__all__':
                            form.add_error(None, ValidationError(errors_list))
                        else:
                            form.add_error(field, ValidationError(errors_list))
                else:
                    form.add_error(None, ValidationError(error_message_list))
            except Exception as e:
                messages.error(request, f"An unexpected error occurred during saving: {e}")
                form.add_error(None, "An unexpected server error occurred.")

    else: # GET
        form = SupplierDeliveryForm()
        formset = formset_helper.get_formset()

    return render(request, 'delivery_form.html', {
        'form': form,
        'formset': formset,
        'title': 'Record Delivery'
    })


class DeliveryDetailView(LoginRequiredMixin, DetailView):
    model = SupplierDelivery
    template_name = 'delivery_detail.html'
    context_object_name = 'delivery'

    def get_queryset(self):
        """
        Override get_queryset to include select_related and prefetch_related
        for optimized data retrieval.
        """
        queryset = super().get_queryset().select_related(
            'payment__supplier'
        ).prefetch_related(
            'delivery_items__motorcycle_model',
            'payment__payment_items__motorcycle_model'
        )
        return queryset


@login_required
@transaction.atomic # Keep for the POST part
def delivery_cancel(request, pk):
    delivery = get_object_or_404(SupplierDelivery, pk=pk)

    if delivery.is_cancelled:
        messages.warning(request, f'Delivery "{delivery.delivery_reference}" is already cancelled.')
        return redirect('delivery_detail', pk=delivery.pk)

    if request.method == 'POST':
        payment_to_update = delivery.payment # Get payment before cancelling
        delivery.cancel_delivery() # This model method handles inventory and sets status
        messages.success(request, f'Delivery "{delivery.delivery_reference}" has been cancelled and inventory has been adjusted.')
        
        if payment_to_update:
             payment_to_update.update_completion_status(force_recalculate=True) # Model method
        return redirect('delivery_detail', pk=delivery.pk) # Or delivery_list or payment_detail

    # For GET request
    consequences = [
        "The delivery status will be set to 'Cancelled'.",
        "Inventory changes made by this delivery will be reversed (items restocked).",
        "The status of the associated Supplier Payment may be re-evaluated (e.g., from 'Completed' back to 'Active')."
    ]

    context = {
        'item': delivery,
        'item_type': "Supplier Delivery",
        'item_identifier': delivery.delivery_reference or f"ID {delivery.pk}",
        'cancel_action_url': request.path,
        'back_url': reverse('delivery_detail', kwargs={'pk': delivery.pk}),
        'additional_warning_message': "This action will reverse inventory adjustments and update related payment status.",
        'cancellation_consequences': consequences,
        'action_verb': 'Cancel Delivery'
    }
    return render(request, 'generic_cancel_confirm.html', context)


# Inventory Views
class InventoryListView(LoginRequiredMixin, ListView):
    """List view for inventory"""
    model = Inventory
    template_name = 'inventory_list.html'
    context_object_name = 'inventory_items'
    paginate_by = 20
    
    def get_queryset(self):
        queryset = Inventory.objects.select_related('motorcycle_model').order_by(
            'motorcycle_model__brand',
            'motorcycle_model__name',
        )
        
        # Apply filters
        form = InventoryFilterForm(self.request.GET)
        if form.is_valid():
            if form.cleaned_data['brand']:
                queryset = queryset.filter(
                    motorcycle_model__brand__icontains=form.cleaned_data['brand']
                )
            
            if form.cleaned_data['model_name']:
                queryset = queryset.filter(
                    motorcycle_model__name__icontains=form.cleaned_data['model_name']
                )
            
            if form.cleaned_data['min_quantity'] is not None:
                queryset = queryset.filter(
                    current_quantity__gte=form.cleaned_data['min_quantity']
                )
            
            if form.cleaned_data['max_quantity'] is not None:
                queryset = queryset.filter(
                    current_quantity__lte=form.cleaned_data['max_quantity']
                )
        
        return queryset
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['filter_form'] = InventoryFilterForm(self.request.GET)
        context['total_inventory'] = self.get_queryset().aggregate(
            total=Sum('current_quantity')
        )['total'] or 0
        return context


class InventoryDetailView(LoginRequiredMixin, DetailView):
    model = Inventory #
    template_name = 'inventory_detail.html' #
    context_object_name = 'inventory' #

    def get_queryset(self):
        """
        Override get_queryset to include select_related for optimized data retrieval.
        """
        queryset = super().get_queryset().select_related('motorcycle_model') #
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        inventory = self.object # The Inventory instance

        # Get and paginate transaction history
        transactions_list = InventoryTransaction.objects.filter( #
            motorcycle_model=inventory.motorcycle_model #
        ).order_by('-transaction_date') #
        
        paginator = Paginator(transactions_list, 20) # Paginate by 20 transactions per page
        page_number = self.request.GET.get('page') # Get page number from request
        page_obj = paginator.get_page(page_number) # Get the page object
        
        context['transactions'] = page_obj # Add paginated transactions to context
        return context


# AJAX Views for dynamic forms
def get_payment_items(request, payment_id):
    """Get payment items for a specific payment (AJAX)"""
    try:
        payment = SupplierPayment.objects.get(id=payment_id)
        items = []
        
        for item in payment.payment_items.all():
            # Calculate already delivered quantity
            delivered_qty = SupplierDeliveryItem.objects.filter(
                delivery__payment=payment,
                motorcycle_model=item.motorcycle_model,
                delivery__is_cancelled=False
            ).aggregate(total=Sum('delivered_quantity'))['total'] or 0
            
            items.append({
                'id': item.id,
                'motorcycle_model': {
                    'id': item.motorcycle_model.id,
                    'name': str(item.motorcycle_model)
                },
                'expected_quantity': item.expected_quantity,
                'delivered_quantity': delivered_qty,
                'remaining_quantity': item.expected_quantity - delivered_qty,
                'unit_price': float(item.unit_price)
            })
        
        return JsonResponse({'items': items})
    except SupplierPayment.DoesNotExist:
        return JsonResponse({'error': 'Payment not found'}, status=404)


def validate_payment_total(request):
    """Validate payment total against item costs (AJAX)"""
    if request.method == 'POST':
        import json
        data = json.loads(request.body)
        
        amount_paid = Decimal(str(data.get('amount_paid', 0)))
        items = data.get('items', [])
        
        total_cost = Decimal('0.00')
        for item in items:
            if not item.get('DELETE', False):
                qty = int(item.get('expected_quantity', 0))
                price = Decimal(str(item.get('unit_price', 0)))
                total_cost += qty * price
        
        difference = amount_paid - total_cost
        
        return JsonResponse({
            'total_cost': float(total_cost),
            'amount_paid': float(amount_paid),
            'difference': float(difference),
            'is_valid': difference >= 0
        })
    
    return JsonResponse({'error': 'Invalid request'}, status=400)


class DepositListView(LoginRequiredMixin, ListView):
    model = Deposit
    template_name = 'deposit_list.html'
    context_object_name = 'deposits'
    paginate_by = 20

    def get_queryset(self):
        queryset = Deposit.objects.select_related('customer').order_by('-deposit_date') #
        
        self.filter_form = DepositFilterForm(self.request.GET or None) #
        if self.filter_form.is_valid(): #
            cleaned_data = self.filter_form.cleaned_data #
            if cleaned_data.get('customer'): #
                queryset = queryset.filter(customer=cleaned_data['customer']) #
            if cleaned_data.get('deposit_amount') is not None: #
                queryset = queryset.filter(deposit_amount=cleaned_data['deposit_amount']) #
            if cleaned_data.get('date_from'): #
                queryset = queryset.filter(deposit_date__date__gte=cleaned_data['date_from']) #
            if cleaned_data.get('date_to'): #
                queryset = queryset.filter(deposit_date__date__lte=cleaned_data['date_to']) #
            if cleaned_data.get('deposit_type'): #
                queryset = queryset.filter(deposit_type=cleaned_data['deposit_type']) #
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not hasattr(self, 'filter_form'):
            self.filter_form = DepositFilterForm(self.request.GET or None) #
        context['filter_form'] = self.filter_form #
        return context


class DepositDetailView(LoginRequiredMixin, DetailView): # New Detail View
    model = Deposit
    template_name = 'deposit_detail.html'
    context_object_name = 'deposit'

    # def get_context_data(self, **kwargs):
    #     context = super().get_context_data(**kwargs)
    #     # Get withdrawals related to this deposit
    #     context['withdrawals'] = self.object.withdrawal_set.order_by('-withdrawal_date')
    #     return context

    def get_queryset(self):
        # Pre-fetch the related customer to avoid a separate query
        return super().get_queryset().select_related('customer')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Optimized way to get withdrawals:
        # self.object is the deposit instance, already fetched with its customer
        context['withdrawals'] = self.object.withdrawal_set.select_related('sale').order_by('-withdrawal_date')
        return context
    

@login_required
def add_deposit(request):
    if request.method == 'POST':
        form = DepositForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    deposit = form.save()
                    messages.success(request, f"Deposit {deposit.deposit_reference} added successfully.")
                    return redirect(deposit.get_absolute_url()) # Redirect to detail view
            except Exception as e:
                messages.error(request, f"Error adding deposit: {str(e)}")
        else:
            messages.error(request, "Please correct the errors in the deposit form.")

    else:
        form = DepositForm()
    return render(request, 'deposit_form.html', {'form': form, 'title': 'Add New Deposit'})


@login_required
def edit_deposit(request, deposit_id):
    deposit = get_object_or_404(Deposit, pk=deposit_id)
    
    # Prevent editing if deposit is cancelled
    if deposit.deposit_status == 'cancelled':
        messages.warning(request, f"Cannot edit Deposit {deposit.deposit_reference} as it is cancelled.")
        return redirect(deposit.get_absolute_url()) # Redirect to detail view


    if request.method == 'POST':
        form = DepositForm(request.POST, instance=deposit)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_deposit = form.save()
                    messages.success(request, f"Deposit {updated_deposit.deposit_reference} updated successfully.")
                    
                    # Check if status was auto-updated and inform user
                    if updated_deposit.deposit_status == 'completed':
                        total_withdrawn = updated_deposit.get_total_withdrawn()
                        if total_withdrawn >= updated_deposit.deposit_amount:

                            messages.info(request, f"Deposit {updated_deposit.deposit_reference} status automatically set to 'Completed' as total withdrawals equal deposit amount.")
                    return redirect(updated_deposit.get_absolute_url())
            except Exception as e:
                messages.error(request, f"Error updating deposit: {str(e)}")
    else:
        form = DepositForm(instance=deposit)

    return render(request, 'deposit_form.html', {
        'form': form, 
        'title': f'Edit Deposit: {deposit.deposit_reference}',
        'edit_mode': True, 
        'action': 'Edit',
        'deposit_instance': deposit # Pass instance for template if needed
        }
    )


@login_required
def cancel_deposit(request, deposit_id):
    """Enhanced deposit cancellation with completed status consideration"""
    deposit = get_object_or_404(Deposit, pk=deposit_id)

    if deposit.deposit_status == 'cancelled':
        messages.warning(request, f"Deposit {deposit.deposit_reference} is already cancelled.")
        return redirect(deposit.get_absolute_url())

    # Check if there are associated withdrawals that would be affected
    deposit_withdrawals = Withdrawal.objects.filter(deposit=deposit, withdrawal_status='completed')
    total_withdrawn_from_deposit = deposit.get_total_withdrawn()
    if total_withdrawn_from_deposit > Decimal('0.00'):
        customer_overall_balance = Withdrawal.get_customer_balance(deposit.customer)
        balance_after_cancellation = customer_overall_balance - deposit.deposit_amount
        if balance_after_cancellation < Decimal('0.00'):
            messages.error(request, 
                f"Cannot cancel Deposit {deposit.deposit_reference}. "
                f"This would result in a negative overall customer balance of ₦{abs(balance_after_cancellation):,.2f}."
            )
            return redirect(deposit.get_absolute_url()) # Redirect to detail view

    if request.method == 'POST':
        try:
            with transaction.atomic():
                deposit.deposit_status = 'cancelled'
                timestamp = timezone.now().strftime('%Y-%m-%d %H:%M')
                cancellation_note = f" Cancelled on {timestamp} by {request.user.username if request.user.is_authenticated else 'system'}."
                if total_withdrawn_from_deposit > Decimal('0.00'):
                    cancellation_note += f" (₦{total_withdrawn_from_deposit:,.2f} had been withdrawn from this deposit.)"
                deposit.transaction_note = (deposit.transaction_note or '') + cancellation_note
                deposit.save(update_fields=['deposit_status', 'transaction_note', 'updated_at']) # Ensure updated_at is also saved
                messages.success(request, f"Deposit {deposit.deposit_reference} has been successfully cancelled.")
                return redirect('deposit_list') # Redirect to list view after cancellation
        except Exception as e:
            messages.error(request, f"Error cancelling deposit: {str(e)}")
            return redirect(deposit.get_absolute_url()) # Back to detail on error

    consequences = [
        "The deposit will be marked as 'Cancelled' and will no longer contribute to the customer's available balance.",
    ]
    if total_withdrawn_from_deposit > Decimal('0.00'):
        consequences.append(f"Withdrawals previously made from this deposit (totaling ₦{total_withdrawn_from_deposit:,.2f}) will remain as completed withdrawals against the customer's overall account history.")

    context = {
        'item': deposit,
        'item_type': "Customer Deposit",
        'item_identifier': deposit.deposit_reference or f"ID {deposit.pk}",
        'cancel_action_url': request.path,
        'back_url': deposit.get_absolute_url(),
        'additional_warning_message': "Ensure this cancellation does not conflict with ongoing or completed sales that relied on this deposit if not automatically handled by sales cancellation processes.",
        'cancellation_consequences': consequences
    }
    return render(request, 'generic_cancel_confirm.html', context)


class WithdrawalListView(LoginRequiredMixin, ListView):
    model = Withdrawal
    template_name = 'withdrawal_list.html'
    context_object_name = 'withdrawals'
    paginate_by = 20

    def get_queryset(self):
        queryset = Withdrawal.objects.select_related('deposit__customer', 'sale').order_by('-withdrawal_date') #

        self.filter_form = WithdrawalFilterForm(self.request.GET or None) #
        if self.filter_form.is_valid(): #
            cleaned_data = self.filter_form.cleaned_data #
            if cleaned_data.get('deposit'): #
                queryset = queryset.filter(deposit=cleaned_data['deposit']) #
            if cleaned_data.get('withdrawal_amount') is not None: #
                queryset = queryset.filter(withdrawal_amount=cleaned_data['withdrawal_amount']) #
            if cleaned_data.get('date_from'): #
                queryset = queryset.filter(withdrawal_date__date__gte=cleaned_data['date_from']) #
            if cleaned_data.get('date_to'): #
                queryset = queryset.filter(withdrawal_date__date__lte=cleaned_data['date_to']) #
            if cleaned_data.get('withdrawal_status'): #
                queryset = queryset.filter(withdrawal_status=cleaned_data['withdrawal_status']) #
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not hasattr(self, 'filter_form'):
            self.filter_form = WithdrawalFilterForm(self.request.GET or None) #
        context['filter_form'] = self.filter_form #
        return context
    

class WithdrawalDetailView(LoginRequiredMixin, DetailView): # New Detail View
    model = Withdrawal
    template_name = 'withdrawal_detail.html'
    context_object_name = 'withdrawal'


@login_required
def add_withdrawal(request):
    if request.method == 'POST':
        form = WithdrawalForm(request.POST)
        if form.is_valid():
            if form.cleaned_data.get('deposit'):
           
                try:
                    with transaction.atomic():
                        withdrawal = form.save()
                        deposit = withdrawal.deposit
                        status_message = f"Withdrawal from {deposit.deposit_reference} processed successfully."
                        
                        if deposit.deposit_status == 'completed':
                            total_withdrawn = deposit.get_total_withdrawn()
                            if total_withdrawn >= deposit.deposit_amount:
                                status_message += f" Deposit status automatically updated to 'Completed'."
                        
                        messages.success(request, status_message)
                        redirect_url = withdrawal.get_absolute_url()
                        return redirect(redirect_url)                
                except Exception as e:
                    messages.error(request, f"Error processing withdrawal: {str(e)}")
        else:
            messages.error(request, "Please correct the errors in the withdrawal form.")
    else:
        form = WithdrawalForm()

    return render(request, 'withdrawal_form.html', {'form': form, 'title': 'Add New Withdrawal'})


@login_required
def edit_withdrawal(request, withdrawal_id):
    withdrawal = get_object_or_404(Withdrawal, pk=withdrawal_id)
    
    # Prevent editing if withdrawal is cancelled
    if withdrawal.withdrawal_status == 'cancelled':
        messages.warning(request, f"Cannot edit Withdrawal from {withdrawal.deposit.deposit_reference} as it is cancelled.")
        return redirect(withdrawal.get_absolute_url())

    if withdrawal.sale: # If the 'sale' ForeignKey is set
        messages.error(request, 
                       f"This withdrawal (ID: {withdrawal.pk}) is linked to Sale '{withdrawal.sale.sale_reference}' "
                       f"and cannot be edited directly. Please manage changes through the Sale record itself.")
        return redirect(withdrawal.get_absolute_url()) # Redirect back to the withdrawal detail page

    # Store original values for status change detection
    original_amount = withdrawal.withdrawal_amount
    original_deposit = withdrawal.deposit

    if request.method == 'POST':
        form = WithdrawalForm(request.POST, instance=withdrawal)
        if form.is_valid():
            try:
                with transaction.atomic():
                    updated_withdrawal = form.save()
                    
                    base_message = f"Withdrawal from {updated_withdrawal.deposit.deposit_reference} updated successfully."
                    
                    # Check for status changes in affected deposits
                    deposits_to_check = {updated_withdrawal.deposit}
                    if original_deposit != updated_withdrawal.deposit:
                        deposits_to_check.add(original_deposit)
                    
                    status_updates = []
                    for deposit in deposits_to_check:
                        if deposit.deposit_status == 'completed':
                            total_withdrawn = deposit.get_total_withdrawn()
                            if total_withdrawn >= deposit.deposit_amount:
                                status_updates.append(f"Deposit {deposit.deposit_reference} is now 'Completed'")
                        elif deposit.deposit_status == 'active':
                            status_updates.append(f"Deposit {deposit.deposit_reference} is now 'Active'")
                    
                    if status_updates:
                        base_message += " " + "; ".join(status_updates) + "."
                    
                    messages.success(request, base_message)
                    return redirect(updated_withdrawal.get_absolute_url()) # Redirect to detail view
            except Exception as e:
                messages.error(request, f"Error updating withdrawal: {str(e)}")
        else:
            messages.error(request, "Please correct the errors in the withdrawal form.")
    else:
        form = WithdrawalForm(instance=withdrawal)

    return render(request, 'withdrawal_form.html', {
        'form': form, 
        'title': f'Edit Withdrawal (ID: {withdrawal.pk})',
        'withdrawal_instance': withdrawal # Pass instance for template if needed
    })


@login_required
def cancel_withdrawal(request, withdrawal_id):
    """Enhanced withdrawal cancellation with status sync"""
    withdrawal = get_object_or_404(Withdrawal, pk=withdrawal_id)

    if withdrawal.withdrawal_status == 'cancelled':
        messages.warning(request, f"Withdrawal from {withdrawal.deposit.deposit_reference} is already cancelled.")
        return redirect(withdrawal.get_absolute_url())

    if withdrawal.sale and withdrawal.sale.status == 'ACTIVE':
        messages.error(request, f"This withdrawal is linked to Active Sale '{withdrawal.sale.sale_reference}'. Please cancel the sale first if you need to reverse this withdrawal.")
        return redirect(withdrawal.get_absolute_url())
    
    if request.method == 'POST':
        print("about to cancel withdrawal")
        try:
            with transaction.atomic():
                # Store deposit for status checking
                deposit = withdrawal.deposit
                was_completed = deposit.deposit_status == 'completed'
                
                withdrawal.withdrawal_status = 'cancelled'
                timestamp = timezone.now().strftime('%Y-%m-%d %H:%M')
                withdrawal.remarks = (withdrawal.remarks or '') + f" Cancelled on {timestamp}"
                withdrawal.save()  # This will trigger deposit status update automatically
                
                # Check if deposit status changed
                deposit.refresh_from_db()
                status_message = f"Withdrawal from {deposit.deposit_reference} has been successfully cancelled."
                
                if was_completed and deposit.deposit_status == 'active':
                    status_message += f" Deposit status changed from 'Completed' to 'Active'."
                
                messages.success(request, status_message)
                return redirect('withdrawal_list')
        except Exception as e:
            messages.error(request, f"Error cancelling withdrawal: {str(e)}")
            return redirect(withdrawal.get_absolute_url()) # Back to detail on error


    consequences = [
            "The withdrawal will be marked as 'Cancelled'.",
            f"The status of the parent deposit '{withdrawal.deposit.deposit_reference}' will be re-evaluated and may change (e.g., from 'Completed' back to 'Active').",
        ]
        
    if withdrawal.sale:
        consequences.append(f"Note: This withdrawal was originally linked to Sale '{withdrawal.sale.sale_reference}'. Cancelling this withdrawal alone does not cancel the sale.")


    context = {
        'item': withdrawal,
        'item_type': "Customer Withdrawal",
        'item_identifier': f"ID {withdrawal.pk} from {withdrawal.deposit.deposit_reference}",
        'cancel_action_url': request.path,
        'back_url': withdrawal.get_absolute_url(),
        'additional_warning_message': "This action will mark the withdrawal as cancelled and may affect the parent deposit's status.",
        'cancellation_consequences': consequences
    }
    return render(request, 'generic_cancel_confirm.html', context)


class MotorcycleListView(LoginRequiredMixin, ListView):
    model = Motorcycle
    template_name = 'motorcycle_list.html'
    context_object_name = 'motorcycles' # This will be the page_obj in template
    paginate_by = 20

    def get_queryset(self):
        queryset = Motorcycle.objects.all()
        self.filter_form = MotorcycleFilterForm(self.request.GET or None)

        if self.filter_form.is_valid():
            cleaned_data = self.filter_form.cleaned_data
            if cleaned_data.get('name'):
                queryset = queryset.filter(name__icontains=cleaned_data['name'])
            if cleaned_data.get('brand'):
                queryset = queryset.filter(brand__icontains=cleaned_data['brand'])
            if cleaned_data.get('status'):
                queryset = queryset.filter(status=cleaned_data['status'])
            # By default, you might want to show only ACTIVE unless a filter is specified
            elif not self.request.GET.get('status'): # If status filter is empty show active by default
                 queryset = queryset.filter(status=Motorcycle.ACTIVE)

        return queryset.order_by('status', 'brand', 'name') # Order by status then brand/name

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not hasattr(self, 'filter_form'):
             self.filter_form = MotorcycleFilterForm(self.request.GET or None)
        context['filter_form'] = self.filter_form
        # context['status_choices'] = Motorcycle.STATUS_CHOICES # Already in filter_form for template
        return context


class MotorcycleDetailView(LoginRequiredMixin, DetailView):
    model = Motorcycle
    template_name = 'motorcycle_detail.html'
    context_object_name = 'motorcycle'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        motorcycle = self.object
        try:
            context['inventory'] = Inventory.objects.filter(motorcycle_model=motorcycle).first()
        except Inventory.DoesNotExist: # Should be .first() so no DoesNotExist if using filter
            context['inventory'] = None
        
        context['recent_sales'] = Sale.objects.filter(motorcycle=motorcycle).order_by('-sale_date')[:5]
        
        can_discontinue, discontinue_reason = motorcycle.can_be_discontinued
        context['can_be_discontinued'] = can_discontinue
        context['discontinuation_warning'] = discontinue_reason if not can_discontinue else None

        has_deps, dep_reason = motorcycle.has_critical_dependencies
        context['can_be_hard_deleted'] = not has_deps
        context['hard_delete_warning'] = dep_reason if has_deps else None
        
        return context


@login_required
def add_motorcycle(request):
    if request.method == 'POST':
        form = MotorcycleForm(request.POST)
        if form.is_valid():
            motorcycle = form.save(commit=False)
            motorcycle.status = Motorcycle.ACTIVE # Explicitly set for new ones
            motorcycle.save()
            messages.success(request, f"Motorcycle '{motorcycle}' created successfully.")
            return redirect(motorcycle.get_absolute_url())
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = MotorcycleForm()
    return render(request, 'motorcycle_form.html', {
        'form': form, 
        'title': 'Add New Motorcycle Model',
        'form_mode': 'create'
    })


@login_required
def edit_motorcycle(request, pk):
    motorcycle = get_object_or_404(Motorcycle, pk=pk)
    if motorcycle.status == Motorcycle.DISCONTINUED:
        messages.warning(request, f"Discontinued motorcycle '{motorcycle}' cannot be edited.")
        return redirect(motorcycle.get_absolute_url())

    if request.method == 'POST':
        form = MotorcycleForm(request.POST, instance=motorcycle)
        if form.is_valid():
            updated_motorcycle = form.save()
            messages.success(request, f"Motorcycle '{updated_motorcycle}' updated successfully.")
            return redirect(updated_motorcycle.get_absolute_url())
        else:
            messages.error(request, "Please correct the errors below.")
    else:
        form = MotorcycleForm(instance=motorcycle)
    return render(request, 'motorcycle_form.html', {
        'form': form,
        'motorcycle': motorcycle,
        'title': f'Edit Motorcycle: {motorcycle}',
        'form_mode': 'edit'
    })


@login_required
def motorcycle_discontinue_view(request, pk):
    motorcycle = get_object_or_404(Motorcycle, pk=pk)

    if motorcycle.status == Motorcycle.DISCONTINUED:
        messages.info(request, f"Motorcycle '{motorcycle}' is already discontinued.")
        return redirect(motorcycle.get_absolute_url())

    can_discontinue, reason = motorcycle.can_be_discontinued
    if not can_discontinue:
        messages.error(request, reason or f"Cannot discontinue '{motorcycle}' due to active dependencies (e.g., outstanding supplier orders).")
        return redirect(motorcycle.get_absolute_url())

    if request.method == 'POST':
        with transaction.atomic():
            motorcycle.status = Motorcycle.DISCONTINUED
            motorcycle.updated_at = timezone.now() # Manually update if auto_now isn't sufficient or if save fields specified
            motorcycle.save(update_fields=['status', 'updated_at'])
            messages.success(request, f"Motorcycle '{motorcycle}' has been discontinued.")
            return redirect(motorcycle.get_absolute_url())

    consequences = [
        "The model will be marked as 'Discontinued'.",
        "It will no longer be available for new sales or supplier orders.",
        "Existing records referencing this model will remain.",
    ]
    inventory_item = Inventory.objects.filter(motorcycle_model=motorcycle).first()
    if inventory_item and inventory_item.current_quantity > 0:
        consequences.append(f"Current stock of {inventory_item.current_quantity} units needs separate management (e.g., sell off, write off).")

    context = {
        'item': motorcycle,
        'item_type': "Motorcycle Model",
        'item_identifier': str(motorcycle),
        'cancel_action_url': request.path,
        'back_url': motorcycle.get_absolute_url(),
        'additional_warning_message': "Discontinuing makes this model unavailable for future use.",
        'cancellation_consequences': consequences,
        'action_verb': 'Discontinue'
    }
    return render(request, 'generic_cancel_confirm.html', context)


@login_required
def motorcycle_delete_permanently_view(request, pk):
    motorcycle = get_object_or_404(Motorcycle, pk=pk)

    has_deps, reason = motorcycle.has_critical_dependencies
    if has_deps:
        messages.error(request, f"Cannot permanently delete '{motorcycle}'. {reason} Consider discontinuing it instead if you wish to remove it from active use.")
        return redirect(motorcycle.get_absolute_url())

    if request.method == 'POST':
        try:
            with transaction.atomic():
                motorcycle_name = str(motorcycle) # Get name before deletion for message
                motorcycle.delete()
                messages.success(request, f"Motorcycle '{motorcycle_name}' has been permanently deleted.")
                return redirect('motorcycle_list') # Redirect to list after successful deletion
        except IntegrityError as e: # Catch ProtectedError which is a subclass of IntegrityError
             messages.error(request, f"Could not delete '{motorcycle}'. It is still referenced by other records: {e}")
             return redirect(motorcycle.get_absolute_url())


    context = {
        'item': motorcycle,
        'item_type': "Motorcycle Model (for Permanent Deletion)",
        'item_identifier': str(motorcycle),
        'cancel_action_url': request.path,
        'back_url': motorcycle.get_absolute_url(),
        'additional_warning_message': "This action will PERMANENTLY remove the motorcycle model. This cannot be undone and is only allowed if the model has no sales, no inventory history, and no stock.",
        'cancellation_consequences': ["The model will be completely erased from the database."],
        'action_verb': 'Delete Permanently'
    }
    return render(request, 'generic_cancel_confirm.html', context)


# --- Loan Views ---
class LoanListView(LoginRequiredMixin, ListView):
    model = Loan
    template_name = 'loan_list.html'
    context_object_name = 'loans'
    paginate_by = 20

    def get_queryset(self):
        queryset = Loan.objects.select_related('customer', 'sale').order_by('-loan_date')
        self.filter_form = LoanFilterForm(self.request.GET or None)

        if self.filter_form.is_valid():
            cleaned_data = self.filter_form.cleaned_data
            if cleaned_data.get('customer'):
                queryset = queryset.filter(customer=cleaned_data['customer'])
            if cleaned_data.get('date_from'):
                queryset = queryset.filter(loan_date__date__gte=cleaned_data['date_from'])
            if cleaned_data.get('date_to'):
                queryset = queryset.filter(loan_date__date__lte=cleaned_data['date_to'])
            if cleaned_data.get('status'):
                queryset = queryset.filter(loan_status=cleaned_data['status'])
            if cleaned_data.get('min_amount') is not None:
                queryset = queryset.filter(loan_amount__gte=cleaned_data['min_amount'])
            if cleaned_data.get('max_amount') is not None:
                queryset = queryset.filter(loan_amount__lte=cleaned_data['max_amount'])
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not hasattr(self, 'filter_form'):
            self.filter_form = LoanFilterForm(self.request.GET or None)
        context['filter_form'] = self.filter_form
        return context


class LoanDetailView(LoginRequiredMixin, DetailView):
    model = Loan
    template_name = 'loan_detail.html'
    context_object_name = 'loan'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['repayments'] = self.object.loanrepayment_set.all().order_by('-repayment_date')
        return context


@login_required
def add_loan(request): # Based on your existing view
    if request.method == 'POST':
        form = LoanForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic(): # Ensure atomicity
                    loan = form.save() # Model's save handles balance, status, reference
                    messages.success(request, f"Loan {loan.loan_reference} added successfully.")
                    return redirect(loan.get_absolute_url())
            except Exception as e:
                 messages.error(request, f"Error adding loan: {str(e)}")
        else:
            messages.error(request, "Please correct the errors in the loan form.")
    else:
        form = LoanForm()
    return render(request, 'loan_form.html', {'form': form, 'title': 'Add New Loan', 'form_mode': 'create'})


@login_required
def edit_loan(request, loan_id): # Based on your existing view
    loan = get_object_or_404(Loan, id=loan_id)

    if loan.loan_status in ['repaid', 'cancelled']:
        messages.warning(request, f"Cannot edit Loan {loan.loan_reference} as it is {loan.get_loan_status_display()}.")
        return redirect(loan.get_absolute_url())
    
    if loan.sale and loan.sale.status == Sale.ACTIVE: # Use model choices: Sale.ACTIVE or 'ACTIVE'
        messages.error(request, 
                       f"Loan {loan.loan_reference} is linked to an active Sale ({loan.sale.sale_reference}). "
                       f"Please cancel the Sale instead.")
        return redirect(loan.get_absolute_url())
    
    if request.method == 'POST':
        form = LoanForm(request.POST, instance=loan)
        if form.is_valid():
            try:
                with transaction.atomic(): # Ensure atomicity
                    updated_loan = form.save() # Model's save handles balance updates if amount changes
                    messages.success(request, f"Loan {updated_loan.loan_reference} updated successfully.")
                    return redirect(updated_loan.get_absolute_url())
            except Exception as e:
                 messages.error(request, f"Error updating loan: {str(e)}")
        else:
            messages.error(request, "Please correct the errors in the loan form.")
    else:
        form = LoanForm(instance=loan)
    return render(request, 'loan_form.html', {
        'form': form, 
        'loan': loan, # For template context if needed
        'title': f'Edit Loan: {loan.loan_reference}',
        'form_mode': 'edit'
    })

@login_required
def cancel_loan(request, loan_id): # Based on your existing view
    loan = get_object_or_404(Loan, id=loan_id)

    if loan.loan_status == 'cancelled':
        messages.warning(request, f"Loan {loan.loan_reference} is already cancelled.")
        return redirect(loan.get_absolute_url())
    
    if loan.loan_status == 'repaid':
        messages.error(request, f"Loan {loan.loan_reference} is fully repaid and cannot be cancelled.")
        return redirect(loan.get_absolute_url())
    
    if loan.sale and loan.sale.status == Sale.ACTIVE: # Use model choices: Sale.ACTIVE or 'ACTIVE'
        messages.error(request, 
                       f"Loan {loan.loan_reference} is linked to an active Sale ({loan.sale.sale_reference}). "
                       f"Please cancel the Sale first if you need to reverse this loan.")
        return redirect(loan.get_absolute_url())
    
    # Add check for associated repayments.
    # If business rule is to not allow cancellation if repayments exist, add check here.
    # For now, if repayments exist, they will remain, but the loan is cancelled.
    # This might require manual adjustment or specific accounting.
    has_repayments = loan.loanrepayment_set.exists()

    if request.method == 'POST':
        try:
            with transaction.atomic():
                loan.loan_status = 'cancelled'
                loan.remarks = (loan.remarks or '') + f" (Cancelled on {timezone.now().strftime('%Y-%m-%d %H:%M')} by {request.user.username if request.user.is_authenticated else 'system'})"
                loan.balance = Decimal('0.00') # Optionally reset balance
                loan.save(update_fields=['loan_status', 'remarks', 'updated_at']) # Add 'balance' if resetting
                messages.success(request, f"Loan {loan.loan_reference} has been successfully cancelled.")
                return redirect(loan.get_absolute_url()) # Redirect to detail after cancellation
        except Exception as e:
            messages.error(request, f"Error cancelling loan: {str(e)}")
            return redirect(loan.get_absolute_url())

    consequences = [
        "The loan will be marked as 'Cancelled'.",
        "No further repayments will be expected or allowed for this loan.",
    ]
    if has_repayments:
        consequences.append("Existing repayments for this loan will remain in the system for historical record.")
    if loan.sale:
        consequences.append(f"This loan was linked to Sale '{loan.sale.sale_reference}'. Cancelling the loan does not automatically cancel the sale.")


    context = {
        'item': loan,
        'item_type': "Loan",
        'item_identifier': loan.loan_reference or f"ID {loan.pk}",
        'cancel_action_url': request.path,
        'back_url': loan.get_absolute_url(),
        'additional_warning_message': "This action will mark the loan as cancelled.",
        'cancellation_consequences': consequences,
        'action_verb': 'Cancel Loan'
    }
    return render(request, 'generic_cancel_confirm.html', context)


# --- Loan Repayment Views ---
class LoanRepaymentListView(LoginRequiredMixin, ListView):
    model = LoanRepayment
    template_name = 'loan_repayment_list.html'
    context_object_name = 'repayments'
    paginate_by = 20

    def get_queryset(self):
        queryset = LoanRepayment.objects.select_related('loan__customer').order_by('-repayment_date')
        self.filter_form = LoanRepaymentFilterForm(self.request.GET or None)

        if self.filter_form.is_valid():
            cleaned_data = self.filter_form.cleaned_data
            if cleaned_data.get('loan'):
                queryset = queryset.filter(loan=cleaned_data['loan'])
            if cleaned_data.get('customer'):
                queryset = queryset.filter(loan__customer=cleaned_data['customer'])
            if cleaned_data.get('date_from'):
                queryset = queryset.filter(repayment_date__date__gte=cleaned_data['date_from'])
            if cleaned_data.get('date_to'):
                queryset = queryset.filter(repayment_date__date__lte=cleaned_data['date_to'])
            if cleaned_data.get('min_amount') is not None:
                queryset = queryset.filter(repayment_amount__gte=cleaned_data['min_amount'])
            if cleaned_data.get('max_amount') is not None:
                queryset = queryset.filter(repayment_amount__lte=cleaned_data['max_amount'])
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not hasattr(self, 'filter_form'):
            self.filter_form = LoanRepaymentFilterForm(self.request.GET or None)
        context['filter_form'] = self.filter_form
        return context


class LoanRepaymentDetailView(LoginRequiredMixin, DetailView):
    model = LoanRepayment
    template_name = 'loan_repayment_detail.html'
    context_object_name = 'repayment'


@login_required
def add_loan_repayment(request): # Based on your existing view (name was add_loanrepayment)
    if request.method == 'POST':
        form = LoanRepaymentForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic(): # CRITICAL: Form save has side effects on Loan
                    repayment = form.save()
                    messages.success(request, f"Repayment of ₦{repayment.repayment_amount} for loan {repayment.loan.loan_reference} added successfully.")
                    return redirect(repayment.get_absolute_url()) # Redirect to repayment detail
            except Exception as e:
                messages.error(request, f"Error adding repayment: {str(e)}")
        else:
            messages.error(request, "Please correct the errors in the repayment form.")
    else:
        form = LoanRepaymentForm()
    return render(request, 'loan_repayment_form.html', {'form': form, 'title': 'Add Loan Repayment', 'form_mode': 'create'})


@login_required
def edit_loan_repayment(request, repayment_id): # Based on your existing view (name was edit_loanrepayment)
    repayment = get_object_or_404(LoanRepayment, id=repayment_id)
    if repayment.loan.loan_status in ['repaid', 'cancelled']:
        messages.warning(request, f"Cannot edit repayment for Loan {repayment.loan.loan_reference} as it is {repayment.loan.get_loan_status_display()}.")
        return redirect(repayment.get_absolute_url())

    if request.method == 'POST':
        form = LoanRepaymentForm(request.POST, instance=repayment)
        if form.is_valid():
            try:
                with transaction.atomic(): # CRITICAL: Form save has side effects on Loan
                    updated_repayment = form.save()
                    messages.success(request, f"Repayment for loan {updated_repayment.loan.loan_reference} updated successfully.")
                    return redirect(updated_repayment.get_absolute_url())
            except Exception as e:
                 messages.error(request, f"Error updating repayment: {str(e)}")
        else:
            messages.error(request, "Please correct the errors in the repayment form.")
    else:
        form = LoanRepaymentForm(instance=repayment)
    return render(request, 'loan_repayment_form.html', {
        'form': form, 
        'repayment': repayment, # For template context
        'title': f'Edit Repayment for Loan: {repayment.loan.loan_reference}',
        'form_mode': 'edit'
    })


@login_required
def delete_loan_repayment(request, repayment_id): # Based on your existing view (name was delete_loanrepayment)
    repayment = get_object_or_404(LoanRepayment, id=repayment_id)
    loan_associated = repayment.loan # Get before deleting repayment

    if loan_associated.loan_status in ['repaid', 'cancelled']:
        messages.warning(request, f"Cannot delete repayment for Loan {loan_associated.loan_reference} as it is {loan_associated.get_loan_status_display()}.")
        return redirect(repayment.get_absolute_url()) # Redirect to repayment detail

    if request.method == 'POST':
        try:
            with transaction.atomic():
                repayment_amount_to_reverse = repayment.repayment_amount
                repayment_str = str(repayment) # Get string representation before delete
                
                repayment.delete() # Hard delete the repayment

                # Reverse the repayment effect on the associated loan balance
                loan_associated.update_balance(-repayment_amount_to_reverse) # Pass negative amount to add it back

                messages.success(request, f"Repayment '{repayment_str}' deleted successfully and loan balance adjusted.")
                return redirect('loan_repayment_list') # Or loan_associated.get_absolute_url()
        except Exception as e:
            messages.error(request, f"Error deleting repayment: {str(e)}")
            return redirect(repayment.get_absolute_url())


    context = {
        'item': repayment,
        'item_type': "Loan Repayment",
        'item_identifier': str(repayment),
        'cancel_action_url': request.path,
        'back_url': repayment.get_absolute_url(),
        'additional_warning_message': "This action will PERMANENTLY delete this repayment record and adjust the parent loan's balance accordingly.",
        'cancellation_consequences': [
            "The repayment record will be erased.",
            f"The balance of Loan '{loan_associated.loan_reference}' will increase by ₦{repayment.repayment_amount:,.2f}.",
            f"The status of Loan '{loan_associated.loan_reference}' may change (e.g., from 'Repaid' to 'Partially Repaid' or 'Pending')."
        ],
        'action_verb': 'Delete Permanently'
    }
    return render(request, 'generic_cancel_confirm.html', context)


def generate_sale_reference(): #
    today_str = timezone.now().strftime('%Y%m%d') #
    last_sale_id = Sale.objects.count() # Simplistic, consider a better sequence #
    return f"SALE-{today_str}-{last_sale_id + 1:04d}" #


def _process_deposit_payment(sale_instance, customer, amount_needed):
    """
    Handles withdrawal from customer deposits.
    Returns True if successful, False otherwise.
    Raises ValidationError if balance insufficient (should be caught by form, but as safeguard).
    """
    # This logic implements the smart withdrawal from multiple deposits [cite: 6]
    active_deposits = Deposit.objects.filter(
        customer=customer, 
        deposit_status__in=['active']
    ).order_by('deposit_date') # Oldest first

    # Calculate overall available balance first
    total_available_balance = Decimal('0.00')
    for deposit in active_deposits:
        balance_on_this_deposit = deposit.deposit_amount - deposit.get_total_withdrawn()
        if balance_on_this_deposit > Decimal('0.00'):
            total_available_balance += balance_on_this_deposit
    
    if total_available_balance < amount_needed:
        # This should ideally be caught by the form's clean method
        raise ValidationError(f"Insufficient total deposit balance. Need {amount_needed}, have {total_available_balance}.")

    amount_to_cover = amount_needed
    withdrawals_to_create = []

    for deposit in active_deposits:
        if amount_to_cover <= Decimal('0.00'):
            break
        
        balance_on_this_deposit = deposit.deposit_amount - deposit.get_total_withdrawn()
        if balance_on_this_deposit <= Decimal('0.00'):
            continue

        amount_from_this_deposit = min(balance_on_this_deposit, amount_to_cover)
        
        new_withdrawal = Withdrawal(
            deposit=deposit,
            withdrawal_amount=amount_from_this_deposit,
            withdrawal_date=timezone.now(),
            remarks=f"Payment for Sale {sale_instance.sale_reference} (Motorcycle Eng: {sale_instance.engine_no})",
            withdrawal_status='completed',
            sale=sale_instance 
        )
        withdrawals_to_create.append(new_withdrawal)
        amount_to_cover -= amount_from_this_deposit

    if amount_to_cover > Decimal('0.00'): # Should not happen if initial check was correct
        return False # Or raise error

    for wd in withdrawals_to_create:
        wd.save() # This will trigger deposit status updates
    return True


class SaleListView(LoginRequiredMixin, ListView):
    model = Sale
    template_name = 'sale_list.html'
    context_object_name = 'sales'
    paginate_by = 20

    def get_queryset(self):
        queryset = Sale.objects.select_related('customer', 'motorcycle').order_by('-sale_date', '-created_at') #

        self.filter_form = SaleFilterForm(self.request.GET or None) #
        if self.filter_form.is_valid(): #
            cleaned_data = self.filter_form.cleaned_data #
            if cleaned_data.get('customer'): #
                queryset = queryset.filter(customer=cleaned_data['customer']) #
            if cleaned_data.get('motorcycle'): #
                queryset = queryset.filter(motorcycle=cleaned_data['motorcycle']) #
            if cleaned_data.get('engine_no'): #
                queryset = queryset.filter(engine_no__icontains=cleaned_data['engine_no']) #
            if cleaned_data.get('chassis_no'): #
                queryset = queryset.filter(chassis_no__icontains=cleaned_data['chassis_no']) #
            if cleaned_data.get('final_price') is not None: #
                queryset = queryset.filter(final_price=cleaned_data['final_price']) #
            if cleaned_data.get('date_from'): #
                queryset = queryset.filter(sale_date__gte=cleaned_data['date_from']) #
            if cleaned_data.get('date_to'): #
                queryset = queryset.filter(sale_date__lte=cleaned_data['date_to']) #
            if cleaned_data.get('payment_type'): #
                queryset = queryset.filter(payment_type=cleaned_data['payment_type']) #
            if cleaned_data.get('status'): #
                queryset = queryset.filter(status=cleaned_data['status']) #
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not hasattr(self, 'filter_form'):
            self.filter_form = SaleFilterForm(self.request.GET or None) #
        context['filter_form'] = self.filter_form #
        return context


class SaleDetailView(LoginRequiredMixin, DetailView):
    model = Sale
    template_name = 'sale_detail.html' # Create this template
    context_object_name = 'sale'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        sale = self.object
        # Get related withdrawals if payment type was deposit
        if sale.payment_type == 'DEPOSIT':
            context['related_withdrawals'] = Withdrawal.objects.filter(sale=sale)
        # Get related loan if payment type was loan
        if sale.payment_type == 'LOAN':
            context['related_loan'] = Loan.objects.filter(sale=sale).first()
        context['inventory_transactions'] = InventoryTransaction.objects.filter(reference_model='Sale', reference_id=sale.pk)
        return context


@login_required
@transaction.atomic # Ensure all operations are atomic
def sale_create_view(request):
    if request.method == 'POST':
        form = SaleCreateForm(request.POST)
        if form.is_valid():
            try:
                sale = form.save(commit=False)
                sale.sale_reference = generate_sale_reference() # Generate and assign reference

                # --- Payment Processing ---
                payment_successful = True
                if sale.payment_type == 'DEPOSIT': # [cite: 1]
                    # This function needs the sale instance (even if not fully saved, for reference in remarks)
                    # but will be fully linked after sale.save()
                    # The form already validated if total balance is sufficient
                    # We pass sale here which is not yet saved. The actual linking of FK is done after sale.save()
                    # For now, let's assume sale is saved first, then payment processed.
                    # Or, pass data to _process_deposit_payment and link after sale.save()
                    pass # Placeholder, see below

                elif sale.payment_type == 'LOAN': # [cite: 1, 8]
                    pass # Placeholder, see below
                
                # Save the sale first to get a PK, which might be needed for related objects
                sale.save() 
                form.save_m2m() # If there were any many-to-many fields

                # Now process payments that need the sale_id
                if sale.payment_type == 'DEPOSIT':
                    if not _process_deposit_payment(sale, sale.customer, sale.final_price): # [cite: 5]
                        # This case should ideally be caught by form validation or early checks
                        payment_successful = False
                        messages.error(request, "Failed to process deposit payment fully despite initial checks.")
                        # transaction will be rolled back by the exception or explicit handling
                        raise ValidationError("Deposit processing failed.") 
                
                elif sale.payment_type == 'LOAN':
                    Loan.objects.create(
                        customer=sale.customer,
                        loan_amount=sale.final_price,
                        remarks=f"Loan for Sale {sale.sale_reference} (Motorcycle Eng: {sale.engine_no})", # [cite: 9]
                        sale=sale, # Link to the sale
                        loan_status='pending' # Or 'active' based on business rule
                    )

                if not payment_successful: # Should have raised error before this
                    raise Exception("Payment processing failed unexpectedly.")


                # --- Inventory Management --- [cite: 12]
                InventoryTransaction.objects.create(
                    transaction_type='SALE', # [cite: 13]
                    motorcycle_model=sale.motorcycle,
                    quantity= -1, # Reduce quantity
                    reference_model='Sale',
                    reference_id=sale.pk,
                    remarks=f"Sale: {sale.sale_reference}, Eng: {sale.engine_no}"
                )
                # The Inventory.update_inventory signal will handle Inventory.current_quantity update

                messages.success(request, f"Sale {sale.sale_reference} created successfully.")
                return redirect(sale.get_absolute_url())
            
            except ValidationError as ve: # Catch validation errors from service logic
                 form.add_error(None, ve) # Add to non-field errors or specific field
                 messages.error(request, "Please correct the errors below.")
            except Exception as e:
                messages.error(request, f"An unexpected error occurred: {e}")
        else:
            messages.error(request, "Please correct the errors in the form.")
    else:
        form = SaleCreateForm()

    return render(request, 'sale_form.html', {'form': form, 'title': 'Create New Sale'})


@login_required
@transaction.atomic
def sale_edit_view(request, pk):
    sale = get_object_or_404(Sale, pk=pk)

    if sale.status == 'CANCELLED':
        messages.error(request, "Cancelled sales cannot be edited.")
        return redirect(sale.get_absolute_url())
    
    # According to user clarification, these fields are not directly editable after creation.
    # 'final_price', 'payment_type', 'motorcycle'
    # The form `SaleEditForm` only allows editing 'sale_date', 'engine_no', 'chassis_no', 'remarks'. [cite: 1]

    if request.method == 'POST':
        form = SaleEditForm(request.POST, instance=sale)
        print(f"POST data received: {request.POST}") # DEBUG
        if form.is_valid():
            print("Form is VALID. Attempting to save...") # DEBUG
            try:
                edited_sale = form.save()
                print(f"Sale PK {edited_sale.pk} saved successfully.") # DEBUG
                messages.success(request, f"Sale {edited_sale.sale_reference} updated successfully.")
                return redirect(edited_sale.get_absolute_url())
            except Exception as e:
                print(f"ERROR during form.save(): {e}") # DEBUG
                messages.error(request, f"An error occurred while updating the sale: {e}")
        else:
            print(f"Form is INVALID. Errors: {form.errors.as_json()}") # DEBUG
            messages.error(request, "Please correct the errors in the form.")
    else:
        form = SaleEditForm(instance=sale)
        print("GET request for edit form.") # DEBUG

    # Pass non-editable fields for display in template
    context = {
        'form': form,
        'sale': sale, # Pass the full sale object for display of non-editable fields
        'title': f'Edit Sale: {sale.sale_reference}'
    }
    return render(request, 'sale_form.html', context)


@login_required
@transaction.atomic
def sale_cancel_view(request, pk):
    sale = get_object_or_404(Sale, pk=pk)

    if sale.status == 'CANCELLED':
        messages.warning(request, f"Sale {sale.sale_reference} is already cancelled.")
        return redirect(sale.get_absolute_url())

    if request.method == 'POST': # Confirmation of cancellation
        try:
            original_payment_type = sale.payment_type
            
            # 1. Update Sale Status
            sale.status = 'CANCELLED'
            sale.remarks = (sale.remarks or "") + f"\nCancelled on {timezone.now().strftime('%Y-%m-%d %H:%M')}."
            sale.save()

            # 2. Revert Inventory Changes [cite: 14]
            InventoryTransaction.objects.create(
                transaction_type='SALE_REVERSAL',
                motorcycle_model=sale.motorcycle,
                quantity=1, # Increase quantity (restock)
                reference_model='Sale',
                reference_id=sale.pk,
                remarks=f"Reversal for cancelled Sale: {sale.sale_reference}"
            )

            # 3. Cancel Associated Financial Transactions [cite: 14]
            if original_payment_type == 'DEPOSIT':
                withdrawals_to_cancel = Withdrawal.objects.filter(sale=sale, withdrawal_status='completed')
                for wd in withdrawals_to_cancel:
                    wd.withdrawal_status = 'cancelled'
                    wd.remarks = (wd.remarks or "") + f"\nReversed due to Sale {sale.sale_reference} cancellation."
                    wd.save() # This will trigger deposit.update_status_based_on_withdrawals()

            elif original_payment_type == 'LOAN':
                loan_to_cancel = Loan.objects.filter(sale=sale).first()
                if loan_to_cancel:
                    # Consider implications if loan has repayments already.
                    # For simplicity, setting to cancelled. Business might need more complex handling.
                    if loan_to_cancel.loan_status not in ['repaid', 'cancelled']: # Check if it can be cancelled
                        loan_to_cancel.loan_status = 'cancelled'
                        loan_to_cancel.remarks = f"\nCancelled due to Sale {sale.sale_reference} cancellation."
                        # loan_to_cancel.balance = Decimal('0.00') # Optionally reset balance if no repayments are to be considered
                        loan_to_cancel.save()
                    else:
                        messages.warning(request, f"Associated loan {loan_to_cancel.loan_reference} is already '{loan_to_cancel.get_loan_status_display()}' and was not altered further.")


            messages.success(request, f"Sale {sale.sale_reference} has been cancelled successfully.")
            return redirect(sale.get_absolute_url())

        except Exception as e:
            messages.error(request, f"An error occurred while cancelling the sale: {e}")
            return redirect(sale.get_absolute_url()) # Or to a list view

    consequences = [
        f"The inventory for '{sale.motorcycle}' will be restocked (+1).",
    ]
    if sale.payment_type == 'DEPOSIT':
        consequences.append("Associated withdrawals made from customer deposits for this sale will be marked as 'cancelled', which may reactivate or adjust the status of those deposits.")
    elif sale.payment_type == 'LOAN':
        consequences.append("The loan record specifically created for this sale will be marked as 'cancelled'.")

    context = {
        'item': sale,
        'item_type': "Sale Record", # User-friendly type name
        'item_identifier': sale.sale_reference or f"Sale ID {sale.pk}", # User-friendly identifier
        'cancel_action_url': request.path, # The current URL will handle the POST request
        'back_url': sale.get_absolute_url() if hasattr(sale, 'get_absolute_url') else reverse('sale_list'), # URL to go back to
        'additional_warning_message': "This action will reverse associated inventory and financial transactions linked to this specific sale.",
        'cancellation_consequences': consequences
    }

    # If GET, show confirmation page
    return render(request, 'generic_cancel_confirm.html', context)


class ActivityLogView(LoginRequiredMixin, TemplateView):
    template_name = 'reports/activity_log.html'
    paginate_by = 20

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        
        # 1. Handle Filtering
        defaults = {'period': 'today', 'view_type': 'summary'}
        filter_form = ActivityLogFilterForm(self.request.GET or defaults)
        
        period = 'today'
        view_type = 'summary'

        if filter_form.is_valid():
            period = filter_form.cleaned_data['period']
            view_type = filter_form.cleaned_data['view_type']
        
        is_summary_view = (view_type == 'summary')

        # 2. Calculate Date Range
        today = timezone.now().date()
        if period == 'today':
            start_date = today
            end_date = today + datetime.timedelta(days=1)
        elif period == 'yesterday':
            start_date = today - datetime.timedelta(days=1)
            end_date = today
        elif period == 'this_week':
            start_date = today - datetime.timedelta(days=today.weekday())
            end_date = start_date + datetime.timedelta(days=7)
        elif period == 'last_7_days':
            start_date = today - datetime.timedelta(days=6)
            end_date = today + datetime.timedelta(days=1)
        else: # this_month
            start_date = today.replace(day=1)
            next_month = (start_date + datetime.timedelta(days=32)).replace(day=1)
            end_date = next_month
        
        start_datetime = timezone.make_aware(datetime.datetime.combine(start_date, datetime.datetime.min.time()))
        end_datetime = timezone.make_aware(datetime.datetime.combine(end_date, datetime.datetime.min.time()))

        # 3. Aggregate or Detail Activities
        if is_summary_view:
            # --- SUMMARY VIEW LOGIC ---
            summary_data = {
                'New Sales': {'count': 0, 'total_amount': Decimal('0.00')},
                'New Supplier Payments': {'count': 0, 'total_amount': Decimal('0.00')},
                'New Deliveries': {'count': 0, 'total_amount': None},
                'New Deposits': {'count': 0, 'total_amount': Decimal('0.00')},
                'New Withdrawals': {'count': 0, 'total_amount': Decimal('0.00')},
                'New Loans': {'count': 0, 'total_amount': Decimal('0.00')},
                'New Loan Repayments': {'count': 0, 'total_amount': Decimal('0.00')},
                'Inventory Updates': {'count': 0, 'total_amount': None},
                'New Motorcycle Models': {'count': 0, 'total_amount': None},
                'New Suppliers': {'count': 0, 'total_amount': None},
            }

            sales_qs = Sale.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime)
            summary_data['New Sales']['count'] = sales_qs.count()
            summary_data['New Sales']['total_amount'] = sales_qs.aggregate(total=Sum('final_price'))['total'] or Decimal('0.00')

            payments_qs = SupplierPayment.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime)
            summary_data['New Supplier Payments']['count'] = payments_qs.count()
            summary_data['New Supplier Payments']['total_amount'] = payments_qs.aggregate(total=Sum('amount_paid'))['total'] or Decimal('0.00')

            summary_data['New Deliveries']['count'] = SupplierDelivery.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).count()
            
            deposits_qs = Deposit.objects.filter(deposit_date__gte=start_datetime, deposit_date__lt=end_datetime)
            summary_data['New Deposits']['count'] = deposits_qs.count()
            summary_data['New Deposits']['total_amount'] = deposits_qs.aggregate(total=Sum('deposit_amount'))['total'] or Decimal('0.00')

            withdrawals_qs = Withdrawal.objects.filter(withdrawal_date__gte=start_datetime, withdrawal_date__lt=end_datetime)
            summary_data['New Withdrawals']['count'] = withdrawals_qs.count()
            summary_data['New Withdrawals']['total_amount'] = withdrawals_qs.aggregate(total=Sum('withdrawal_amount'))['total'] or Decimal('0.00')

            loans_qs = Loan.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime)
            summary_data['New Loans']['count'] = loans_qs.count()
            summary_data['New Loans']['total_amount'] = loans_qs.aggregate(total=Sum('loan_amount'))['total'] or Decimal('0.00')

            repayments_qs = LoanRepayment.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime)
            summary_data['New Loan Repayments']['count'] = repayments_qs.count()
            summary_data['New Loan Repayments']['total_amount'] = repayments_qs.aggregate(total=Sum('repayment_amount'))['total'] or Decimal('0.00')
            
            summary_data['Inventory Updates']['count'] = InventoryTransaction.objects.filter(transaction_date__gte=start_datetime, transaction_date__lt=end_datetime).count()
            summary_data['New Motorcycle Models']['count'] = Motorcycle.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).count()
            summary_data['New Suppliers']['count'] = Supplier.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).count()
            
            context['summary_data'] = {k: v for k, v in summary_data.items() if v['count'] > 0}
            context['is_paginated'] = False

        else:
            # --- DETAILED VIEW LOGIC ---
            raw_activities = []
            sales = Sale.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).select_related('customer', 'motorcycle')
            for sale in sales:
                raw_activities.append({'timestamp': sale.created_at, 'activity_type': 'New Sale', 'description': f"Sale <a href='{sale.get_absolute_url()}'>{sale.sale_reference}</a> to {sale.customer.name} for ₦{sale.final_price:,.2f}"})
            payments = SupplierPayment.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).select_related('supplier')
            for p in payments:
                raw_activities.append({'timestamp': p.created_at, 'activity_type': 'New Supplier Payment', 'description': f"Payment <a href='{reverse('payment_detail', args=[p.pk])}'>{p.payment_reference}</a> of ₦{p.amount_paid:,.2f} to {p.supplier.name}"})
            deliveries = SupplierDelivery.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).select_related('payment__supplier')
            for d in deliveries:
                raw_activities.append({'timestamp': d.created_at, 'activity_type': 'New Delivery', 'description': f"Delivery <a href='{d.get_absolute_url()}'>{d.delivery_reference}</a> received from {d.payment.supplier.name}"})
            deposits = Deposit.objects.filter(deposit_date__gte=start_datetime, deposit_date__lt=end_datetime).select_related('customer')
            for d in deposits:
                raw_activities.append({'timestamp': d.deposit_date, 'activity_type': 'New Deposit', 'description': f"Deposit <a href='{d.get_absolute_url()}'>{d.deposit_reference}</a> of ₦{d.deposit_amount:,.2f} from {d.customer.name}"})
            withdrawals = Withdrawal.objects.filter(withdrawal_date__gte=start_datetime, withdrawal_date__lt=end_datetime).select_related('deposit__customer')
            for w in withdrawals:
                raw_activities.append({'timestamp': w.withdrawal_date, 'activity_type': 'New Withdrawal', 'description': f"Withdrawal of ₦{w.withdrawal_amount:,.2f} from {w.deposit.customer.name}'s account (<a href='{w.get_absolute_url()}'>Details</a>)"})
            loans = Loan.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).select_related('customer')
            for l in loans:
                raw_activities.append({'timestamp': l.created_at, 'activity_type': 'New Loan', 'description': f"Loan <a href='{l.get_absolute_url()}'>{l.loan_reference}</a> of ₦{l.loan_amount:,.2f} issued to {l.customer.name}"})
            repayments = LoanRepayment.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime).select_related('loan__customer')
            for r in repayments:
                raw_activities.append({'timestamp': r.created_at, 'activity_type': 'New Loan Repayment', 'description': f"Repayment of ₦{r.repayment_amount:,.2f} for loan {r.loan.loan_reference} by {r.loan.customer.name} (<a href='{r.get_absolute_url()}'>Details</a>)"})
            inv_trans = InventoryTransaction.objects.filter(transaction_date__gte=start_datetime, transaction_date__lt=end_datetime).select_related('motorcycle_model')
            for t in inv_trans:
                raw_activities.append({'timestamp': t.transaction_date, 'activity_type': 'Inventory Update', 'description': f"{t.get_transaction_type_display()}: {t.quantity} units of {t.motorcycle_model}. Remarks: {t.remarks}"})
            motorcycles = Motorcycle.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime)
            for m in motorcycles:
                raw_activities.append({'timestamp': m.created_at, 'activity_type': 'New Motorcycle Model', 'description': f"Model <a href='{m.get_absolute_url()}'>{m}</a> was added to the system."})
            suppliers = Supplier.objects.filter(created_at__gte=start_datetime, created_at__lt=end_datetime)
            for s in suppliers:
                raw_activities.append({'timestamp': s.created_at, 'activity_type': 'New Supplier', 'description': f"Supplier <a href='{reverse('supplier_detail', args=[s.pk])}'>{s.name}</a> was added."})
            
            sorted_activities = sorted(raw_activities, key=itemgetter('timestamp'), reverse=True)
            paginator = Paginator(sorted_activities, self.paginate_by)
            page_number = self.request.GET.get('page')
            page_obj = paginator.get_page(page_number)
            
            context['activities'] = page_obj
            context['page_obj'] = page_obj
            context['is_paginated'] = True if paginator.num_pages > 1 else False

        # 4. Final Context
        context['filter_form'] = filter_form
        context['title'] = f"Activity Log for {period.replace('_', ' ').title()}"
        context['print_mode'] = self.request.GET.get('print', 'false').lower() == 'true'
        context['is_summary_view'] = is_summary_view

        return context